#!/bin/bash
# Vignette — update to the latest code.
#
# Usage:
#   bash scripts/update.sh                 # over SSH
#   Settings → Update Software             # from the web console
#
# The service restart is deliberately handed to a *detached* systemd unit.
# When this script runs from the web console it is a child of vignette.service,
# and `systemctl restart vignette` kills the whole service cgroup — this script
# included, along with the HTTP request waiting on it. The update would land but
# always look like it had failed.

set -euo pipefail

usage() {
    cat <<'EOF'
Vignette — update to the latest code.

  bash scripts/update.sh          Fetch, fast-forward, install deps, restart
  bash scripts/update.sh --repair-refs
                                  Delete local branch refs whose commits are
                                  missing, then update as usual
  bash scripts/update.sh --help   This message

Also reachable from Settings → Update Software. Driven that way it runs as the
service account, which has no SSH key and no terminal — so `origin` has to be
an address that needs neither.
EOF
}

REPAIR_REFS=""
case "${1:-}" in
    -h|--help) usage; exit 0 ;;
    --repair-refs) REPAIR_REFS="yes" ;;
    "") ;;
    *) echo "Unknown option: $1" >&2; echo >&2; usage >&2; exit 2 ;;
esac

INSTALL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$INSTALL_DIR"

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    SUDO="sudo"
fi

# Nothing here may ever wait for a human. From the web console this script is
# a child of vignette.service with no terminal attached, so git's credential
# prompt and SSH's "continue connecting (yes/no)?" do not ask anybody
# anything — they just hang until the request times out fifteen minutes later.
export GIT_TERMINAL_PROMPT=0
export GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -o BatchMode=yes -o ConnectTimeout=10}"

echo "============================================"
echo "  Vignette — Update"
echo "============================================"
echo "Directory: $INSTALL_DIR"

# ── Which branch are we on? ─────────────────────────────────────────────
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$BRANCH" = "HEAD" ]; then
    echo
    echo "ERROR: this checkout is in a detached HEAD state, so there is no"
    echo "       branch to update from. Pick one first, e.g.:"
    echo "           git checkout main"
    exit 1
fi

echo "Branch:    $BRANCH"
echo "Installed: $(git log --oneline -1)"
echo

# ── Refuse to clobber local edits ───────────────────────────────────────
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "ERROR: there are uncommitted changes to tracked files, so updating"
    echo "       would overwrite them. Review them with:"
    echo "           git status"
    echo "       Then either commit them, or discard them with:"
    echo "           git checkout -- ."
    echo
    git status --short
    exit 1
fi

# ── Step 1: fetch and fast-forward ──────────────────────────────────────
#
# The web console runs this as the service account: a system user created with
# --system and /usr/sbin/nologin, whose home holds no SSH key and no
# known_hosts. An `origin` on SSH therefore fails with "Host key verification
# failed" however well SSH is set up for the login account — the key in *your*
# ~/.ssh belongs to a different user. For a public repository HTTPS needs no
# credentials at all, so that is the address to be on.

ORIGIN_URL="$(git remote get-url origin 2>/dev/null || true)"
FETCH_OUTPUT=""
HTTPS_URL=""

https_url_for() {
    # git@github.com:owner/repo.git   -> https://github.com/owner/repo.git
    # ssh://git@github.com/owner/repo -> https://github.com/owner/repo
    case "$1" in
        ssh://git@*) printf '%s\n' "$1" | sed -E 's#^ssh://git@([^/]+)/#https://\1/#' ;;
        git@*:*)     printf '%s\n' "$1" | sed -E 's#^git@([^:]+):#https://\1/#' ;;
        *)           printf '' ;;
    esac
}

explain_remote_failure() {
    # The checkout belongs to the service account, so its owner *is* the
    # account that fetches when the web console asks for an update — and the
    # account any hand-run git command has to be run as, or git refuses the
    # working tree as having dubious ownership.
    local owner
    owner="$(stat -c '%U' "$INSTALL_DIR" 2>/dev/null || id -un)"

    # "Could not read from remote repository" is also what a missing local path
    # produces, so the SSH advice is only right when origin really is SSH — or
    # when git named an SSH-specific problem outright.
    local looks_like_ssh=""
    case "$ORIGIN_URL" in
        git@*:*|ssh://*) looks_like_ssh="yes" ;;
    esac
    case "$FETCH_OUTPUT" in
        *"Host key verification failed"*|*publickey*) looks_like_ssh="yes" ;;
    esac

    echo
    echo "ERROR: could not reach origin (${ORIGIN_URL:-no remote configured})."
    echo

    if [ -n "$looks_like_ssh" ]; then
        echo "       git is using SSH, and it cannot authenticate. From the web"
        echo "       console the fetch is done by '$owner', which owns this"
        echo "       checkout and has no SSH key or known_hosts of its own — so"
        echo "       a key in your login account's ~/.ssh is never consulted."
        echo "       (This run: $(id -un).)"
        echo
        if [ -n "$HTTPS_URL" ]; then
            echo "       $HTTPS_URL did not answer either,"
            echo "       so either this device is offline or the repository is"
            echo "       private."
        else
            echo "       If the repository is public, fetching over HTTPS needs no"
            echo "       credentials at all:"
            echo "           sudo -u $owner git -C $INSTALL_DIR remote set-url origin \\"
            echo "               https://github.com/OWNER/REPO.git"
        fi
        echo
        echo "       For a private repository: install a deploy key for '$owner',"
        echo "       or use an HTTPS URL carrying a token."
        return
    fi

    case "$FETCH_OUTPUT" in
        *"bad object refs/"*|*"did not send all necessary objects"*)
            # Reached only if a ref broke between the check above and here.
            echo "       Despite the message, origin answered: a local ref names a"
            echo "       commit this checkout no longer has, and git fails the"
            echo "       fetch over it. Clear them with:"
            echo "           bash scripts/update.sh --repair-refs"
            ;;
        *"Could not resolve host"*|*"Network is unreachable"*|*"unable to access"*|*"Connection timed out"*|*"timed out"*)
            echo "       The device cannot reach the network at the moment. Check"
            echo "       the WiFi connection and try again."
            ;;
        *)
            echo "       git reported the above; nothing was changed."
            ;;
    esac
}

# ── A local ref git cannot resolve stops every fetch ────────────────────
#
# git checks all local refs while fetching, so one branch pointing at a commit
# that is no longer in the object database fails the whole update — with
# "fatal: bad object refs/heads/<name>" and "did not send all necessary
# objects", which reads like a network fault and is not one. A branch left over
# from work that has since been merged is the way this happens: the branch is
# deleted upstream, the local ref survives, and eventually the commit behind it
# is pruned. There is nothing to recover from such a ref — its commit is gone —
# but deleting a branch is still the owner's call, so this reports and offers
# --repair-refs rather than doing it unasked.
BROKEN_REFS=""
while IFS= read -r ref; do
    [ -n "$ref" ] || continue
    git rev-parse --verify --quiet "$ref^{object}" >/dev/null 2>&1 && continue
    BROKEN_REFS="${BROKEN_REFS}${ref}"$'\n'
done <<EOF
$(git for-each-ref --format='%(refname)' refs/heads refs/remotes refs/tags 2>/dev/null || true)
EOF

if [ -n "$BROKEN_REFS" ]; then
    if [ -n "$REPAIR_REFS" ]; then
        echo "[0/3] Removing local refs whose commits are missing..."
        printf '%s' "$BROKEN_REFS" | while IFS= read -r ref; do
            [ -n "$ref" ] || continue
            echo "      $ref"
            git update-ref -d "$ref" || true
        done
        echo
    else
        OWNER="$(stat -c '%U' "$INSTALL_DIR" 2>/dev/null || id -un)"
        echo
        echo "ERROR: this checkout has refs pointing at commits it no longer has:"
        printf '%s' "$BROKEN_REFS" | sed 's/^/           /'
        echo
        echo "       git checks every ref while fetching, so these block the"
        echo "       update even though origin is reachable and '$BRANCH' itself"
        echo "       is fine. Nothing can be recovered from them — the commits"
        echo "       they name are already gone. To delete them and update:"
        echo "           sudo -u $OWNER bash scripts/update.sh --repair-refs"
        exit 1
    fi
fi

echo "[1/3] Fetching from origin..."
if ! FETCH_OUTPUT="$(git fetch --prune origin 2>&1)"; then
    printf '%s\n' "$FETCH_OUTPUT"
    HTTPS_URL="$(https_url_for "$ORIGIN_URL")"

    # Only rewrite the remote once the replacement has been shown to work, so a
    # private repository (where HTTPS without a token fails too) is left exactly
    # as it was rather than swapped for something equally broken.
    if [ -n "$HTTPS_URL" ] && git ls-remote --exit-code "$HTTPS_URL" HEAD >/dev/null 2>&1; then
        echo
        echo "SSH is not usable from this account, and the same repository"
        echo "answers over HTTPS. Pointing 'origin' at:"
        echo "    $HTTPS_URL"
        git remote set-url origin "$HTTPS_URL"
        ORIGIN_URL="$HTTPS_URL"
        echo
        if ! FETCH_OUTPUT="$(git fetch --prune origin 2>&1)"; then
            printf '%s\n' "$FETCH_OUTPUT"
            explain_remote_failure
            exit 1
        fi
    else
        explain_remote_failure
        exit 1
    fi
fi

BEFORE="$(git rev-parse HEAD)"

if ! git merge --ff-only "origin/$BRANCH"; then
    echo
    echo "ERROR: cannot fast-forward $BRANCH onto origin/$BRANCH."
    echo "       The device has commits that origin does not, so this needs a"
    echo "       human. Inspect with:"
    echo "           git log --oneline HEAD ^origin/$BRANCH"
    exit 1
fi

AFTER="$(git rev-parse HEAD)"

if [ "$BEFORE" = "$AFTER" ]; then
    echo
    echo "Already up to date — nothing to install, no restart needed."
    exit 0
fi

echo
echo "Applied:"
git --no-pager log --oneline "$BEFORE..$AFTER"
echo

# ── Step 2: dependencies ────────────────────────────────────────────────
echo "[2/3] Updating Python dependencies..."
if [ -x venv/bin/pip ]; then
    # Only reinstall when the requirements actually moved: pip on a Pi Zero is
    # slow enough that skipping it saves most of the update.
    if git diff --name-only "$BEFORE" "$AFTER" | grep -qx "requirements.txt"; then
        venv/bin/pip install -r requirements.txt --quiet
        echo "Dependencies updated."
    else
        echo "requirements.txt unchanged — skipped."
    fi
else
    echo "WARNING: venv/bin/pip not found. Dependencies were not updated."
fi
echo

# ── Step 3: restart, detached from this process ─────────────────────────
echo "[3/3] Restarting the Vignette service..."

restart_detached() {
    # A transient unit lives outside vignette.service's cgroup, so the restart
    # cannot take this script (or the web request) down with it. The delay
    # gives the HTTP response time to reach the browser first.
    if command -v systemd-run >/dev/null 2>&1; then
        $SUDO systemd-run --collect --quiet \
            --unit="vignette-restart-$$" \
            --description="Restart Vignette after an update" \
            /bin/sh -c 'sleep 3; systemctl restart vignette' && return 0
    fi
    # Fallback for systems without systemd-run: a new session detaches the
    # child from this process group.
    $SUDO setsid nohup /bin/sh -c 'sleep 3; systemctl restart vignette' \
        >/dev/null 2>&1 &
}

if $SUDO systemctl is-enabled --quiet vignette 2>/dev/null || \
   $SUDO systemctl is-active --quiet vignette 2>/dev/null; then
    restart_detached
    echo "Restart scheduled — the service comes back in a few seconds."
else
    echo "vignette.service is not installed or running."
    echo "Start it with: sudo systemctl start vignette"
fi

echo
echo "Update complete."
echo "Now running: $(git log --oneline -1)"

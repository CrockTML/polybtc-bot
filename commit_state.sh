#!/bin/bash
# Commit bot state back to the repo. Trims decisions.jsonl to the last 500 lines.
cd "$(dirname "$0")"
[ -f decisions.jsonl ] && tail -n 500 decisions.jsonl > d.tmp && mv d.tmp decisions.jsonl
git config user.name "polybtc-bot"; git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
[ -f push.log ] && tail -n 300 push.log | grep -v -i "bearer" > push_tail.txt
[ -f run.log ] && tail -n 100 run.log > run_tail.txt
git add push_tail.txt run_tail.txt push_status.txt 2>/dev/null
git add state.json trades.jsonl hist_done.json decisions.jsonl
if ! git diff --cached --quiet; then
git commit -qm "state $(date -u +%FT%TZ)"
ok=0; for i in 1 2 3; do git pull -q --rebase -X theirs origin main 2>/dev/null; git push -q origin HEAD:main && { ok=1; break; }; sleep 5; done
[ $ok = 1 ] || echo "push failed"
else git pull -q --rebase -X theirs origin main 2>/dev/null || true
fi
# hot-reload push.py on the runner if it changed in the repo (keeps the dashboard pusher current without restarting the bot)
if [ -n "$GITHUB_ACTIONS" ] && [ -n "$ABACUS_KEYS" ]; then
  cur=$(md5sum push.py | cut -d" " -f1)
  if [ "$(cat .push_md5 2>/dev/null)" != "$cur" ]; then
    echo "$cur" > .push_md5
    pkill -f "python3 -u push.py" || true; sleep 1
    setsid nohup sh -c 'python3 -u push.py 2>&1 | sed -u "s/^/[push] /" >> push.log' >/dev/null 2>&1 < /dev/null &
    echo "push.py reloaded"
  fi
fi

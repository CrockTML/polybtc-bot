#!/bin/bash
# Commit bot state back to the repo. Trims decisions.jsonl to the last 500 lines.
cd "$(dirname "$0")"
[ -f decisions.jsonl ] && tail -n 500 decisions.jsonl > d.tmp && mv d.tmp decisions.jsonl
git config user.name "polybtc-bot"; git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
[ -f push.log ] && tail -n 300 push.log | grep -v -i "bearer" > push_tail.log
[ -f run.log ] && tail -n 100 run.log > run_tail.log
git add push_tail.log run_tail.log 2>/dev/null
git add state.json trades.jsonl hist_done.json decisions.jsonl
git diff --cached --quiet && exit 0
git commit -qm "state $(date -u +%FT%TZ)"
for i in 1 2 3; do git pull -q --rebase -X theirs origin main 2>/dev/null; git push -q origin HEAD:main && exit 0; sleep 5; done
echo "push failed"

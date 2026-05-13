

# ── Octave ─────────────────────────────────────────────────────
alias octave="cd ~/docker/octave"
alias octave-up="cd ~/docker/octave && docker compose up -d"
alias octave-down="cd ~/docker/octave && docker compose down"
alias octave-logs="cd ~/docker/octave && docker compose logs -f --tail=50"
alias octave-sync="curl -s -X POST http://localhost:8000/api/sync/all | python3 -m json.tool"
alias octave-status="curl -s http://localhost:8000/api/sync/status | python3 -m json.tool"
alias octave-build="cd ~/docker/octave && docker compose build"
alias octave-shell="docker exec -it octave /bin/bash"

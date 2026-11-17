#!/usr/bin/env bash
set -euo pipefail
docker_cli="${DOCKER:-docker}"
image="${1:-the-zookeeper:0.1.0-gpu}"
volume="zk-smoke-$(date +%s)-$$"
container="zk-smoke-$$"
"$docker_cli" volume create "$volume" >/dev/null
cleanup() { "$docker_cli" rm -f "$container" >/dev/null 2>&1 || true; "$docker_cli" volume rm "$volume" >/dev/null 2>&1 || true; }
trap cleanup EXIT
# No network at all. HTTP requests below happen over loopback inside the container.
"$docker_cli" run -d --name "$container" --network none -v "$volume:/data" "$image" >/dev/null
for n in $(seq 1 40); do
  if "$docker_cli" exec "$container" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')" 2>/dev/null; then break; fi
  sleep 1
done
"$docker_cli" exec "$container" python -m zookeeper.demo /data/demo.parquet
"$docker_cli" exec -i "$container" python - <<'PY'
import json,time,urllib.request
from pathlib import Path
from zookeeper.ingest import ingest,new_snapshot
from zookeeper.search import audit
from zookeeper.storage import Store
s=Store('/data');sid=new_snapshot(s,'container demo');ingest(s,sid,Path('/data/demo.parquet'),lambda *_:None)
r=audit(s,sid,{'source':{'split':'test'},'reference':{'split':'train'},'mode':'exact_cpu'},lambda *_:None)
assert r['searchable']==5
with s.db() as c:c.execute("INSERT INTO reviews VALUES(?,?,?,'leakage','confirmed leakage','persistent volume check',?)",(sid,0,1,time.time()))
Path('/data/smoke-snapshot').write_text(sid)
assert urllib.request.urlopen('http://127.0.0.1:8000/').status==200
s.pool.shutdown()
PY
"$docker_cli" restart "$container" >/dev/null
"$docker_cli" exec -i "$container" python - <<'PY'
from pathlib import Path
from zookeeper.storage import Store
s=Store('/data');sid=Path('/data/smoke-snapshot').read_text()
assert s.snapshot(sid)['status']=='ready'
with s.db() as c:assert c.execute('SELECT decision FROM reviews WHERE snapshot=?',(sid,)).fetchone()[0]=='confirmed leakage'
assert list((s.path(sid)/'audits').glob('*.parquet'))
s.pool.shutdown()
PY

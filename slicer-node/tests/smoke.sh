#!/bin/sh
# 冒烟测试：健康检查 → 提交切片 → 轮询 → 下载 gcode → 校验层数
# 用法： ./tests/smoke.sh [model.stl] [host:port]
set -e
MODEL="${1:-tests/fixtures/cube.stl}"
BASE="${2:-http://127.0.0.1:8020}"
TMP=$(mktemp -d)
trap 'echo "workdir $TMP"' EXIT

echo "== health =="
curl -fsS "$BASE/health" | python3 -m json.tool

echo "== profiles =="
curl -fsS "$BASE/profiles"

echo "== submit =="
RESP=$(curl -fsS -F "file=@$MODEL" -F "machine=voron24-350" \
  -F "process=020-standard" -F "filament=generic-pla" "$BASE/slice")
echo "$RESP"
JOB=$(printf '%s' "$RESP" | python3 -c 'import sys,json;print(json.load(sys.stdin)["job_id"])')

echo "== poll =="
i=0
while [ $i -lt 120 ]; do
  ST=$(curl -fsS "$BASE/jobs/$JOB")
  STATUS=$(printf '%s' "$ST" | python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])')
  case "$STATUS" in
    done) echo "$ST" | python3 -m json.tool; break ;;
    failed) echo "$ST" | python3 -m json.tool; echo "FAILED"; exit 1 ;;
  esac
  i=$((i+1)); sleep 2
done

echo "== gcode =="
curl -fsS "$BASE/jobs/$JOB/gcode" -o "$TMP/out.gcode"
ls -lh "$TMP/out.gcode"
LAYERS=$(grep -c '^;LAYER_CHANGE' "$TMP/out.gcode" || true)
HEAD=$(head -3 "$TMP/out.gcode" | tail -1)
echo "layers=$LAYERS  header=$HEAD"
[ "$LAYERS" -gt 0 ] || { echo "gcode 里没有层标记，失败"; exit 1; }

echo "== preview index =="
curl -fsS "$BASE/jobs/$JOB/preview" -o "$TMP/idx.json"
python3 -c "import json,sys;d=json.load(open(sys.argv[1]));\
print('layers=%d segs=%d bin=%d' % (d['layer_count'],d['segment_count'],d['bin_bytes']))" "$TMP/idx.json"

echo "== preview layer 0 =="
curl -fsS -D "$TMP/hdr" -o "$TMP/l0.bin" "$BASE/jobs/$JOB/preview/0"
N=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['layers'][0]['count'])" "$TMP/idx.json")
SZ=$(wc -c < "$TMP/l0.bin")
echo "segments=$N bytes=$SZ (期望 $((N * 17)))"
[ "$SZ" = "$((N * 17))" ] || { echo "层二进制长度不对"; exit 1; }

echo "== preview filter types=0 =="
curl -fsS -o "$TMP/l0outer.bin" "$BASE/jobs/$JOB/preview/0?types=0"
OW=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['layers'][0]['types'].get('0',0))" "$TMP/idx.json")
OS=$(wc -c < "$TMP/l0outer.bin")
echo "outer_wall segments=$OW bytes=$OS (期望 $((OW * 17)))"
[ "$OS" = "$((OW * 17))" ] || { echo "过滤后长度不对"; exit 1; }

echo "== preview Range =="
RD=$(python3 -c "import json,sys;l=json.load(open(sys.argv[1]))['layers'][0];print('%d %d' % (l['offset'], l['offset']+l['count']*17-1))" "$TMP/idx.json")
A=${RD% *}; B2=${RD#* }
curl -fsS -H "Range: bytes=$A-$B2" -o "$TMP/l0r.bin" "$BASE/jobs/$JOB/preview/bin"
if cmp -s "$TMP/l0.bin" "$TMP/l0r.bin"; then
  echo "Range 取到的字节与单层接口一致 ($A-$B2)"
else
  echo "Range 内容不一致"; exit 1
fi

echo "== error path =="
CODE=$(curl -s -o /dev/null -w '%{http_code}' -F "file=@$MODEL" -F "machine=nope" \
  -F "process=020-standard" "$BASE/slice")
echo "unknown machine -> HTTP $CODE (期望 400)"
[ "$CODE" = "400" ] || exit 1

echo "SMOKE OK"

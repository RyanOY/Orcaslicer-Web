import io
p = "tools/bump_0112.py"
s = io.open(p, encoding="utf-8").read()
for frag in [
    '    (ROOT + "slicer-node/app/main.py", \'APP_VERSION = "0.11.1"\', \'APP_VERSION = "0.11.2"\'),\n',
    '    (ROOT + "slicer-node/docker-compose.yml", "orca-slicer-node:0.11.1", "orca-slicer-node:0.11.2"),\n',
    '    (ROOT + "slicer-node/Makefile", "VERSION   ?= 0.11.1", "VERSION   ?= 0.11.2"),\n',
]:
    s = s.replace(frag, "")
io.open(p, "w", encoding="utf-8").write(s)
print("patched")

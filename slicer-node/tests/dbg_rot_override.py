import json, subprocess, shutil
from pathlib import Path

PROF = Path("/data/jobs/3a1a055f70e9")
ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "LC_ALL": "C", "LANG": "C",
       "APPDIR": "/opt/orca", "LD_LIBRARY_PATH": "/opt/orca/lib/orca-runtime:/opt/orca/bin",
       "TMPDIR": "/tmp"}
PLATE = "/tmp/an30/plate.3mf"


def run(label, proc_path, idx):
    out = Path("/tmp/ov%d" % idx); out.mkdir(parents=True, exist_ok=True)
    argv = ["/opt/orca/bin/orca-slicer", PLATE, "--slice", "0", "--arrange", "0",
            "--load-settings", "%s;%s" % (proc_path, PROF / "machine.json"),
            "--load-filaments", str(PROF / "filament_1.json"),
            "--outputdir", str(out), "--logfile", str(out / "orca.log")]
    p = subprocess.run(argv, env=ENV, cwd=str(out), capture_output=True, timeout=900)
    print("%-26s rc=%-4s gcode=%s" % (label, p.returncode, (out / "plate_1.gcode").is_file()), flush=True)


base = json.loads((PROF / "process.json").read_text())
for i, (k, v) in enumerate([("enable_support", "1"),
                            ("support_type", "tree(auto)"),
                            ("raft_layers", "1"),
                            ("layer_height", "0.28"),
                            ("brim_type", "outer_only")]):
    d = dict(base); d[k] = v
    p = Path("/tmp/proc_%s.json" % k); p.write_text(json.dumps(d))
    run("override %s=%s" % (k, v), p, 50 + i)

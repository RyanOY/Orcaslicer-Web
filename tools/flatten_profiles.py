#!/usr/bin/env python3
"""把 OrcaSlicer 内置 profile（带 inherits 继承链）展开成单文件 JSON。

为什么需要：OrcaSlicer CLI 的 --load-settings 不解析 inherits，
直接把 resources/profiles 里的原始 profile 丢给 CLI 会报
"run found error, return -17"。SimplyPrint 之类工具导出的
"export-full" 就是展开后的样子，我们也得自己展一遍。

用法：
  python3 flatten_profiles.py --profiles-dir <.../resources/profiles> \
      --vendor Voron --kind machine --name "Voron 2.4 350 0.4 nozzle" -o out.json

  # 列出某 vendor 下可展开的条目
  python3 flatten_profiles.py --profiles-dir ... --vendor Voron --list
"""
import argparse
import json
import os
import sys

LIST_KEYS = {
    "machine": "machine_list",
    "process": "process_list",
    "filament": "filament_list",
}


class Bundle:
    def __init__(self, profiles_dir, vendor):
        self.profiles_dir = profiles_dir
        self.vendor = vendor
        self.vendor_dir = os.path.join(profiles_dir, vendor)
        path = os.path.join(profiles_dir, vendor + ".json")
        if not os.path.exists(path):
            raise SystemExit("找不到 vendor bundle: %s" % path)
        with open(path, encoding="utf-8") as f:
            self.data = json.load(f)
        self.index = {}
        for kind, key in LIST_KEYS.items():
            for entry in self.data.get(key) or []:
                if isinstance(entry, dict) and "name" in entry:
                    self.index[entry["name"]] = entry
        # 机型（printer_model）也算条目
        for entry in self.data.get("machine_model_list") or []:
            if isinstance(entry, dict) and "name" in entry:
                self.index.setdefault(entry["name"], entry)

    def list_names(self):
        for kind, key in LIST_KEYS.items():
            names = [e["name"] for e in (self.data.get(key) or [])
                     if isinstance(e, dict) and "name" in e]
            print("[%s] %d 条" % (kind, len(names)))
            for n in names:
                print("   ", n)

    def load(self, name):
        entry = self.index.get(name)
        if entry is None:
            raise SystemExit("bundle 里没有这个 profile: %r" % name)
        sub = entry.get("sub_path")
        if not sub:
            raise SystemExit("profile %r 没有 sub_path" % name)
        candidates = [
            os.path.join(self.vendor_dir, sub),
            os.path.join(self.profiles_dir, sub),
        ]
        for c in candidates:
            if os.path.exists(c):
                with open(c, encoding="utf-8") as f:
                    return json.load(f)
        # 有些条目只有 sub_path 没有文件，退回用 bundle 内联内容
        inline = {k: v for k, v in entry.items() if k != "sub_path"}
        if len(inline) > 1:
            return inline
        raise SystemExit("找不到 profile 文件: %r (%s)" % (name, candidates))

    def flatten(self, name, _seen=None):
        seen = _seen or []
        if name in seen:
            raise SystemExit("inherits 循环: %s" % " -> ".join(seen + [name]))
        raw = self.load(name)
        parent = raw.get("inherits")
        merged = {}
        if parent:
            merged.update(self.flatten(parent, seen + [name]))
        for k, v in raw.items():
            if k == "inherits":
                continue
            merged[k] = v
        return merged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profiles-dir", required=True)
    ap.add_argument("--vendor", required=True)
    ap.add_argument("--kind", choices=list(LIST_KEYS) + ["auto"], default="auto")
    ap.add_argument("--name")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("-o", "--out")
    args = ap.parse_args()

    bundle = Bundle(args.profiles_dir, args.vendor)
    if args.list or not args.name:
        bundle.list_names()
        return 0

    flat = bundle.flatten(args.name)
    if args.kind != "auto" and flat.get("type") not in (None, args.kind):
        print("警告: profile type=%r，与 --kind=%s 不符" %
              (flat.get("type"), args.kind), file=sys.stderr)

    text = json.dumps(flat, indent=1, ensure_ascii=False, sort_keys=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print("wrote %s (%d keys)" % (args.out, len(flat)))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

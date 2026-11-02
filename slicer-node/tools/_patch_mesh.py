NEW = r'''
PALETTE = ["#FF7D38", "#4D80BA", "#4CAF7D", "#C86BD8", "#E8B23A", "#7EC8E3",
           "#E0564F", "#9AD34F"]

PROJECT_HEAD = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<model unit="millimeter" xml:lang="zh-CN" '
    'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
    'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/02">')

OBJECT_MODEL = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<model unit="millimeter" '
    'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
    '<resources><object id="1" type="model">%s</object></resources>'
    "<build/></model>")


def _indexed_mesh(tris: list[Tri]) -> str:
    """索引化 <mesh>：按坐标去重顶点。

    直接写「每个三角形三个新顶点」的汤，Orca 的 3MF 解析**不会**替你焊接，
    结果 `manifold=no` / 每个三角形算一个 part，切片时报 empty layer。
    所以这里必须自己合并顶点。
    """
    index: dict[tuple[float, float, float], int] = {}
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    for tri in tris:
        idx = []
        for v in tri:
            key = (round(v[0], 4), round(v[1], 4), round(v[2], 4))
            j = index.get(key)
            if j is None:
                j = len(verts)
                index[key] = j
                verts.append(key)
            idx.append(j)
        if len(set(idx)) == 3:
            faces.append((idx[0], idx[1], idx[2]))
    out = ["<mesh><vertices>"]
    out.extend('<vertex x="%.4f" y="%.4f" z="%.4f"/>' % v for v in verts)
    out.append("</vertices><triangles>")
    out.extend('<triangle v1="%d" v2="%d" v3="%d"/>' % f for f in faces)
    out.append("</triangles></mesh>")
    return "".join(out)


def write_3mf(path: Path, objects: list[dict], plate_name: str = "plate",
              filament_docs: list[dict] | None = None) -> None:
    """objects: [{name, tris, extruder}] —— 三角形已是最终坐标（含摆放变换）。

    单耗材 → 朴素 3MF（对象内联 mesh，Orca 当模型文件导入）。
    多耗材 → Orca 工程布局（3dmodel 里只有 <components>，网格拆到
    3D/Objects/*.model，并带 Metadata/model_settings.config + project_settings
    .config）。实测：多耗材时用朴素布局会让 OrcaSlicer 2.4.2 **直接段错误**，
    工程布局才走得通 —— 这也是 Bambu/Orca 自己存盘的样子。
    """
    multi = any((o.get("extruder") or 1) > 1 for o in objects)
    if multi:
        _write_project(path, objects, plate_name, filament_docs or [])
    else:
        _write_flat(path, objects, plate_name)


def _write_flat(path: Path, objects: list[dict], plate_name: str) -> None:
    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<model unit="millimeter" xml:lang="zh-CN" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">',
        '<metadata name="Application">orca-web-slicer</metadata>',
        '<metadata name="Title">%s</metadata>' % _xml(plate_name),
        "<resources>",
    ]
    for i, obj in enumerate(objects, start=1):
        parts.append('<object id="%d" type="model" name="%s">' % (i, _xml(obj["name"])))
        parts.append(_indexed_mesh(obj["tris"]))
        parts.append("</object>")
    parts.append("</resources><build>")
    for i in range(1, len(objects) + 1):
        parts.append('<item objectid="%d" printable="1"/>' % i)
    parts.append("</build></model>")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr("_rels/.rels", RELS)
        zf.writestr("3D/3dmodel.model", "".join(parts))


def _first(value) -> str:
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else ""
    return "" if value is None else str(value)


def _write_project(path: Path, objects: list[dict], plate_name: str,
                   filament_docs: list[dict]) -> None:
    """Orca/Bambu 工程布局。多耗材必须走这条。"""
    main = [PROJECT_HEAD,
            '<metadata name="Application">orca-web-slicer</metadata>',
            '<metadata name="Title">%s</metadata>' % _xml(plate_name),
            "<resources>"]
    for i, obj in enumerate(objects, start=1):
        main.append('<object id="%d" type="model" name="%s"><components>'
                    '<component objectid="1" p:path="/3D/Objects/object_%d.model"/>'
                    "</components></object>" % (i, _xml(obj["name"]), i))
    main.append("</resources><build>")
    for i in range(1, len(objects) + 1):
        main.append('<item objectid="%d" printable="1"/>' % i)
    main.append("</build></model>")

    cfg = ['<?xml version="1.0" encoding="UTF-8"?>', "<config>"]
    for i, obj in enumerate(objects, start=1):
        cfg.append('<object id="%d">' % i)
        cfg.append('  <metadata key="name" value="%s"/>' % _xml(obj["name"]))
        cfg.append('  <metadata key="extruder" value="%d"/>' % (obj.get("extruder") or 1))
        cfg.append('  <part id="1" subtype="normal_part">')
        cfg.append('    <metadata key="name" value="%s"/>' % _xml(obj["name"]))
        cfg.append("  </part></object>")
    cfg.append("</config>")

    # 工程文件里要有一份「耗材槽」定义，Orca 才知道有几种料。
    # 真正的参数仍由 --load-filaments 覆盖，这里只给数量/颜色/类型。
    count = max([len(filament_docs)] + [(o.get("extruder") or 1) for o in objects] + [1])
    names, colours, types, ids = [], [], [], []
    for i in range(count):
        doc = filament_docs[i] if i < len(filament_docs) and isinstance(filament_docs[i], dict) else {}
        names.append(_first(doc.get("name")) or "Filament %d" % (i + 1))
        colours.append(_first(doc.get("filament_colour")) or PALETTE[i % len(PALETTE)])
        types.append(_first(doc.get("filament_type")) or "PLA")
        ids.append((_first(doc.get("filament_id")) or "F%d" % i).upper())
    project = {
        "filament_settings_id": names,
        "filament_colour": colours,
        "filament_type": types,
        "filament_ids": ids,
        "single_extruder_multi_material": "1",
        "print_settings_id": "orca-web",
    }

    rels = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
    for i in range(1, len(objects) + 1):
        rels.append('<Relationship Target="/3D/Objects/object_%d.model" Id="rel%d" '
                    'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
                    % (i, i + 1))
    rels.append("</Relationships>")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr("_rels/.rels", RELS)
        zf.writestr("3D/3dmodel.model", "".join(main))
        zf.writestr("3D/_rels/3dmodel.model.rels", "".join(rels))
        for i, obj in enumerate(objects, start=1):
            zf.writestr("3D/Objects/object_%d.model" % i,
                        OBJECT_MODEL % _indexed_mesh(obj["tris"]))
        zf.writestr("Metadata/model_settings.config", "".join(cfg))
        zf.writestr("Metadata/project_settings.config",
                    json.dumps(project, ensure_ascii=False, indent=4))


'''

p = "app/mesh.py"
s = open(p).read()
i = s.index("def write_3mf(")
j = s.index("def _xml(")
s = s[:i] + NEW.lstrip("\n") + "\n\n" + s[j:]
open(p, "w").write(s)
print("patched", len(NEW))

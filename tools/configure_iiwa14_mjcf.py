#!/usr/bin/env python3
"""Prepare the pinned MuJoCo Menagerie KUKA iiwa 14 model for the arm runtime.

Changes to the upstream MJCF (all recorded in sources/models/iiwa14/LICENSE_INFO.yaml):
- every joint gets an explicit ``range`` (resolved from its default class), the
  configured ``armature`` and ``actuatorgravcomp="true"``;
- every arm body and the F/T sensor body get ``gravcomp="1"`` so the servos
  carry gravity through the actuators; the tool below the sensor gets none
  (as in the model checked against the recording), so the servos hold it by
  their stiffness. Gravity compensation does not change the wrench the sensor
  measures;
- the ``general`` actuators become ``position`` actuators named after their
  joint with the upstream gains (kp 2000, kv 200) and an explicit ``ctrlrange``;
- an F/T sensor body and a tool body are attached at the flange
  (values from recipes/iiwa14/config/model/wrist-payload.json).
"""

from __future__ import annotations

import argparse
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path


class ConfigureError(ValueError):
    pass


def _fmt(values: list[float] | tuple[float, ...]) -> str:
    return " ".join(repr(float(v)) for v in values)


def _class_defaults(root: ET.Element) -> dict[str, dict[str, dict[str, str]]]:
    """Map class name -> element tag -> resolved attributes (with inheritance)."""
    resolved: dict[str, dict[str, dict[str, str]]] = {}

    def walk(node: ET.Element, inherited: dict[str, dict[str, str]]) -> None:
        for child in node.findall("default"):
            name = child.get("class")
            current = {tag: dict(attrs) for tag, attrs in inherited.items()}
            for element in child:
                if element.tag == "default":
                    continue
                current.setdefault(element.tag, {}).update(element.attrib)
            if name:
                resolved[name] = current
            walk(child, current)

    top = root.find("default")
    if top is None:
        raise ConfigureError("upstream MJCF has no <default> section")
    base = {e.tag: dict(e.attrib) for e in top if e.tag != "default"}
    walk(top, base)
    return resolved


def _joint_class(joint: ET.Element, parents: dict[ET.Element, ET.Element]) -> str | None:
    if joint.get("class"):
        return joint.get("class")
    node = parents.get(joint)
    while node is not None:
        if node.tag == "body" and node.get("childclass"):
            return node.get("childclass")
        node = parents.get(node)
    return None


def configure(root: ET.Element, payload: dict) -> None:
    defaults = _class_defaults(root)
    parents = {child: parent for parent in root.iter() for child in parent}
    worldbody = root.find("worldbody")
    if worldbody is None:
        raise ConfigureError("upstream MJCF has no <worldbody>")

    armature = float(payload["joint_armature"])
    ranges: dict[str, tuple[float, float]] = {}
    for joint in worldbody.iter("joint"):
        name = joint.get("name")
        cls = _joint_class(joint, parents)
        raw = joint.get("range") or defaults.get(cls or "", {}).get("joint", {}).get("range")
        if not name or raw is None:
            raise ConfigureError(f"joint {name!r} has no range")
        lower, upper = (float(v) for v in raw.split())
        ranges[name] = (lower, upper)
        joint.set("range", _fmt((lower, upper)))
        joint.set("armature", repr(armature))
        joint.set("actuatorgravcomp", "true")

    for body in worldbody.iter("body"):
        body.set("gravcomp", "1")

    actuator = root.find("actuator")
    if actuator is None:
        raise ConfigureError("upstream MJCF has no <actuator> section")
    generals = actuator.findall("general")
    if len(generals) != len(ranges):
        raise ConfigureError(f"expected {len(ranges)} general actuators, found {len(generals)}")
    for general in generals:
        joint_name = general.get("joint")
        if joint_name not in ranges:
            raise ConfigureError(f"actuator {general.get('name')!r} drives unknown joint {joint_name!r}")
        gains = defaults.get(general.get("class") or "", {}).get("general", {})
        gainprm = float(gains.get("gainprm", "nan").split()[0])
        biasprm = [float(v) for v in gains.get("biasprm", "").split()]
        if not (math.isfinite(gainprm) and len(biasprm) == 3 and math.isclose(biasprm[1], -gainprm)):
            raise ConfigureError(f"actuator {general.get('name')!r} is not a position servo: {gains}")
        position = ET.Element(
            "position",
            {
                "name": joint_name,
                "joint": joint_name,
                "kp": repr(gainprm),
                "kv": repr(-biasprm[2]),
                "ctrlrange": _fmt(ranges[joint_name]),
            },
        )
        index = list(actuator).index(general)
        actuator.remove(general)
        actuator.insert(index, position)

    parent_name = payload["parent_body"]
    parent = next((b for b in worldbody.iter("body") if b.get("name") == parent_name), None)
    if parent is None:
        raise ConfigureError(f"payload parent body {parent_name!r} not found")
    sensor = payload["sensor"]
    tool = payload["tool"]
    half_height = sensor["height_m"] / 2.0
    yaw = math.radians(float(sensor["yaw_deg"])) / 2.0
    sensor_body = ET.SubElement(
        parent,
        "body",
        {
            "name": sensor["body"],
            "gravcomp": "1",
            "pos": _fmt((0.0, 0.0, payload["flange_offset_m"])),
            "quat": _fmt((math.cos(yaw), 0.0, 0.0, math.sin(yaw))),
        },
    )
    ET.SubElement(
        sensor_body,
        "inertial",
        {"mass": repr(sensor["mass_kg"]), "pos": _fmt((0.0, 0.0, half_height)), "diaginertia": _fmt(sensor["diag_inertia"])},
    )
    ET.SubElement(
        sensor_body,
        "geom",
        {
            "type": "cylinder",
            "size": _fmt((sensor["radius_m"], half_height)),
            "pos": _fmt((0.0, 0.0, half_height)),
            "rgba": "0.2 0.4 0.8 1",
            "contype": "0",
            "conaffinity": "0",
        },
    )
    tool_body = ET.SubElement(
        sensor_body,
        "body",
        {"name": tool["body"], "pos": _fmt((0.0, 0.0, sensor["height_m"]))},
    )
    ET.SubElement(tool_body, "site", {"name": "ft_site", "pos": "0 0 0"})
    ET.SubElement(
        tool_body,
        "inertial",
        {"mass": repr(tool["mass_kg"]), "pos": _fmt(tool["com_m"]), "diaginertia": _fmt(tool["diag_inertia"])},
    )
    ET.SubElement(
        tool_body,
        "geom",
        {
            "type": "box",
            "size": _fmt(tool["half_size_m"]),
            "pos": _fmt((0.0, 0.0, tool["half_size_m"][2])),
            "rgba": "0.5 0.35 0.65 1",
            "contype": "0",
            "conaffinity": "0",
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure the Menagerie iiwa 14 MJCF for the arm runtime")
    parser.add_argument("input", type=Path)
    parser.add_argument("--payload", required=True, type=Path)
    parser.add_argument("--output", "-o", required=True, type=Path)
    args = parser.parse_args()

    tree = ET.parse(args.input)
    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    try:
        configure(tree.getroot(), payload)
    except (ConfigureError, KeyError) as error:
        parser.error(str(error))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(tree, space="  ")
    tree.write(args.output, encoding="utf-8", xml_declaration=False)
    print(f"Configured iiwa 14 MJCF: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

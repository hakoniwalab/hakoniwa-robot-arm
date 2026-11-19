from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import configure_iiwa14_mjcf as iiwa  # noqa: E402
import sync_arm_joint_limits as limits  # noqa: E402


# Minimal model with the same structure as the Menagerie iiwa 14 MJCF:
# joint ranges and servo gains come from nested default classes.
UPSTREAM = """<mujoco model="mini">
  <compiler angle="radian" autolimits="true"/>
  <default>
    <default class="arm">
      <joint axis="0 0 1"/>
      <general gaintype="fixed" biastype="affine" gainprm="2000" biasprm="0 -2000 -200"/>
      <default class="j1">
        <joint range="-2.9 2.9"/>
        <general ctrlrange="-2.9 2.9"/>
        <default class="j2">
          <joint range="-2.0 2.0"/>
          <general ctrlrange="-2.0 2.0"/>
        </default>
      </default>
    </default>
  </default>
  <worldbody>
    <body name="base" childclass="arm">
      <body name="link1">
        <joint name="joint1" class="j1"/>
        <body name="link2">
          <joint name="joint2" class="j2"/>
        </body>
      </body>
    </body>
  </worldbody>
  <actuator>
    <general name="actuator1" joint="joint1" class="j1"/>
    <general name="actuator2" joint="joint2" class="j2"/>
  </actuator>
</mujoco>
"""

PAYLOAD = {
    "parent_body": "link2",
    "flange_offset_m": 0.045,
    "sensor": {
        "body": "ft_sensor",
        "mass_kg": 0.255,
        "radius_m": 0.0375,
        "height_m": 0.0333,
        "diag_inertia": [0.0002, 0.0002, 0.0002],
        "yaw_deg": 0.0,
    },
    "tool": {
        "body": "tool",
        "mass_kg": 0.9323,
        "com_m": [0.0003, 0.0001, 0.0439],
        "half_size_m": [0.03, 0.03, 0.044],
        "diag_inertia": [0.002, 0.002, 0.001],
    },
    "joint_armature": 2.0,
}


def configured() -> ET.Element:
    root = ET.fromstring(UPSTREAM)
    iiwa.configure(root, json.loads(json.dumps(PAYLOAD)))
    return root


def test_joints_get_resolved_range_armature_and_actuator_gravcomp():
    root = configured()
    joints = {j.get("name"): j for j in root.find("worldbody").iter("joint")}
    assert [float(v) for v in joints["joint1"].get("range").split()] == [-2.9, 2.9]
    assert [float(v) for v in joints["joint2"].get("range").split()] == [-2.0, 2.0]
    for joint in joints.values():
        assert float(joint.get("armature")) == 2.0
        assert joint.get("actuatorgravcomp") == "true"


def test_general_servos_become_named_position_actuators_with_upstream_gains():
    root = configured()
    actuator = root.find("actuator")
    assert actuator.findall("general") == []
    positions = actuator.findall("position")
    assert [p.get("name") for p in positions] == ["joint1", "joint2"]
    assert [p.get("joint") for p in positions] == ["joint1", "joint2"]
    for position in positions:
        assert float(position.get("kp")) == 2000.0
        assert float(position.get("kv")) == 200.0
    assert [float(v) for v in positions[1].get("ctrlrange").split()] == [-2.0, 2.0]


def test_payload_is_attached_and_only_the_tool_has_no_gravity_compensation():
    root = configured()
    bodies = {b.get("name"): b for b in root.iter("body")}
    assert bodies["ft_sensor"] in list(bodies["link2"])
    assert bodies["tool"] in list(bodies["ft_sensor"])
    assert bodies["tool"].find("site").get("name") == "ft_site"
    assert float(bodies["tool"].find("inertial").get("mass")) == pytest.approx(0.9323)
    for name, body in bodies.items():
        expected = None if name == "tool" else "1"
        assert body.get("gravcomp") == expected, name


def test_non_servo_actuator_is_rejected():
    root = ET.fromstring(UPSTREAM.replace('biasprm="0 -2000 -200"', 'biasprm="0 0 0"'))
    with pytest.raises(iiwa.ConfigureError):
        iiwa.configure(root, json.loads(json.dumps(PAYLOAD)))


def test_limit_sync_reads_explicit_ranges_from_a_source_mjcf(tmp_path: Path):
    model = tmp_path / "model.xml"
    tree = ET.ElementTree(configured())
    tree.write(model, encoding="utf-8")
    assert limits.read_source_limits(model) == {"joint1": (-2.9, 2.9), "joint2": (-2.0, 2.0)}


def test_limit_sync_rejects_a_source_mjcf_joint_without_range(tmp_path: Path):
    model = tmp_path / "model.xml"
    model.write_text(UPSTREAM, encoding="utf-8")  # ranges only in default classes
    with pytest.raises(limits.JointLimitError):
        limits.read_source_limits(model)


def test_limit_sync_still_reads_urdf(tmp_path: Path):
    urdf = tmp_path / "robot.urdf"
    urdf.write_text(
        '<robot name="r"><joint name="a" type="revolute"><limit lower="-1" upper="1"/></joint></robot>',
        encoding="utf-8",
    )
    assert limits.read_source_limits(urdf) == {"a": (-1.0, 1.0)}

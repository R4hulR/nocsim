"""Characterise router and link energy/area/timing with DSENT for nocsim.power.

Standard library only; run where the standalone DSENT binary is built (see
dsent_main.cc), from inside gem5/ext/dsent's parent directory so DSENT's
relative tech-model paths resolve:

    cd ~/nocsim_ext/gem5
    python3 /path/to/characterize.py --dsent ~/nocsim_ext/dsent --out dsent_22nm.json

Router: 5 ports (4 network + 1 local), also 3 and 4 ports for a mesh's corner
and edge routers; 128-bit flits, 3 VCs x 8-flit buffers
(the configuration used for the application-trace experiments).
Links: repeated global wires, 128 bits wide. For each length DSENT sizes the
repeaters to meet a one-cycle delay; the longest length it can still meet
gives the "reach" of one clock cycle.
"""

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path

CFG_DIR = Path("ext/dsent/configs")
NUM = r"([-+0-9.eE]+|nan|inf)"


def write_cfg(template: str, overrides: dict, query: list, prints: list) -> str:
    """Copy a DSENT config, replacing keys, QueryString and EvaluateString."""
    lines, out, skipping = Path(template).read_text().splitlines(), [], False
    for line in lines:
        if skipping:
            skipping = line.rstrip().endswith("\\")
            continue
        key = line.split("=")[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in ("QueryString", "EvaluateString"):
            skipping = line.rstrip().endswith("\\")
            continue
        if key in overrides:
            line = f"{key} = {overrides[key]}"
        out.append(line)
    out.append("QueryString = " + " ".join(query))
    out.append("EvaluateString = " + " ".join(f'print "{name} " {expr};' for name, expr in prints))
    f = tempfile.NamedTemporaryFile("w", suffix=".cfg", delete=False)
    f.write("\n".join(out) + "\n")
    f.close()
    return f.name


def run(dsent: str, cfg: str) -> dict:
    out = subprocess.run([dsent, cfg], capture_output=True, text=True)
    if "Timing not met" in out.stdout + out.stderr:
        raise RuntimeError("timing not met")
    vals = {}
    for m in re.finditer(r"^(\w+) " + NUM + r"\s*$", out.stdout, re.M):
        vals[m.group(1)] = float(m.group(2))
    if not vals:
        raise RuntimeError(out.stdout[-800:] + out.stderr[-800:])
    return vals


def router(dsent: str, tech: str, freq: float, ports: int = 5) -> dict:
    q = ["Energy>>Router:WriteBuffer@0", "Energy>>Router:ReadBuffer@0",
         "Energy>>Router:TraverseCrossbar->Multicast1@0", "Energy>>Router:ArbitrateSwitch->ArbitrateStage1@0",
         "Energy>>Router:ArbitrateSwitch->ArbitrateStage2@0", "Energy>>Router:DistributeClock@0",
         "NddPower>>Router:Leakage@0", "Area>>Router:Active@0"]
    prints = [("e_write", "$(Energy>>Router:WriteBuffer)"), ("e_read", "$(Energy>>Router:ReadBuffer)"),
              ("e_xbar", "$(Energy>>Router:TraverseCrossbar->Multicast1)"),
              ("e_sa", "$(Energy>>Router:ArbitrateSwitch->ArbitrateStage1) + $(Energy>>Router:ArbitrateSwitch->ArbitrateStage2)"),
              ("e_clock", "$(Energy>>Router:DistributeClock)"),
              ("p_leak", "$(NddPower>>Router:Leakage)"), ("area", "$(Area>>Router:Active)")]
    cfg = write_cfg(CFG_DIR / "router.cfg", {
        "ElectricalTechModelFilename": f"ext/dsent/tech/tech_models/Bulk{tech}LVT.model",
        "Frequency": f"{freq:g}", "NumberInputPorts": str(ports), "NumberOutputPorts": str(ports), "NumberBitsPerFlit": "128",
        "NumberVirtualNetworks": "1", "NumberVirtualChannelsPerVirtualNetwork": "[3]",
        "NumberBuffersPerVirtualChannel": "[8]"}, q, prints)
    return run(dsent, cfg)


def link(dsent: str, tech: str, length_m: float, delay_s: float) -> dict | None:
    q = ["Energy>>RepeatedLink:Send@0", "NddPower>>RepeatedLink:Leakage@0", "Area>>RepeatedLink:Active@0"]
    prints = [("e_send", "$(Energy>>RepeatedLink:Send)"), ("p_leak", "$(NddPower>>RepeatedLink:Leakage)"),
              ("area", "$(Area>>RepeatedLink:Active)")]
    cfg = write_cfg(CFG_DIR / "electrical-link.cfg", {
        "ElectricalTechModelFilename": f"ext/dsent/tech/tech_models/Bulk{tech}LVT.model",
        "NumberBits": "128", "WireLength": f"{length_m:g}", "Delay": f"{delay_s:g}"}, q, prints)
    try:
        return run(dsent, cfg)
    except RuntimeError:
        return None  # DSENT could not meet the delay at this length


def global_wire_pitch(tech: str) -> float:
    """Global-layer wire pitch (m) from DSENT's own technology model."""
    text = Path(f"ext/dsent/tech/tech_models/Bulk{tech}LVT.model").read_text()
    get = lambda k: float(re.search(rf"^Wire->Global->{k}\s*=\s*{NUM}", text, re.M).group(1))
    return get("MinWidth") + get("MinSpacing")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsent", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--techs", default="22,32,45")  # main results use 22 nm
    ap.add_argument("--freqs", default="1e9,2e9,3e9,4e9")
    args = ap.parse_args()

    result = {}
    for tech in args.techs.split(","):
        for freq in (float(f) for f in args.freqs.split(",")):
            key = f"{tech}nm_{freq / 1e9:g}GHz"
            r = router(args.dsent, tech, freq)
            # Routers with fewer network ports (a mesh's corner and edge routers).
            by_ports = {str(k): router(args.dsent, tech, freq, k) for k in (3, 4, 5)}
            # Link energy/leakage/area at several lengths (one-cycle delay), and
            # the longest length that still meets one cycle.
            lens_mm = [0.5 * i for i in range(1, 41)]  # 0.5 .. 20 mm
            links = {}
            for mm in lens_mm:
                v = link(args.dsent, tech, mm * 1e-3, 1.0 / freq)
                if v is not None:
                    links[mm] = v
            reach = max(links) if links else 0.0
            result[key] = {"tech_nm": int(tech), "freq_hz": freq, "router": r, "router_by_ports": by_ports,
                           "link_by_length_mm": {str(k): v for k, v in links.items()},
                           "reach_mm": reach, "global_wire_pitch_m": global_wire_pitch(tech)}
            print(key, "router e/flit ~", r["e_write"] + r["e_read"] + r["e_xbar"] + r["e_sa"],
                  "J | leak", r["p_leak"], "W | area", r["area"], "m2 | link lengths ok:", sorted(links))
    Path(args.out).write_text(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()

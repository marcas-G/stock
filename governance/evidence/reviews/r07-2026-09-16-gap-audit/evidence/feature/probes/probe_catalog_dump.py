import json, subprocess, sys
out = subprocess.run([sys.argv[1], "catalog", "dump"], capture_output=True, text=True)
d = json.loads(out.stdout)
def walk(o, p=""):
    if isinstance(o, dict):
        for k, v in o.items():
            walk(v, f"{p}/{k}")
    elif isinstance(o, list):
        print(f"{p} = {len(o)}")
    elif isinstance(o, str):
        print(f"{p} = {o[:60]}")
walk(d)

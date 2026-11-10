"""A tiny stand-in game server used by the BaoBun tests.

Modes:  normal  - reads console commands until /stop or quit
        crash   - exits with code 3 after half a second
        portcrash - prints a "port in use" error, then exits with code 1
        ignore  - ignores stop commands (tests the forced, whole-tree stop)
Commands: join / leave (write Vintage Story style join/leave lines), spawn (starts a child
process, like a launcher would), /announce <msg>, /autosavenow, /stop, quit
"""
import os
import subprocess
import sys
import time

mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
print("fake server up", mode, flush=True)
if mode == "crash":
    time.sleep(0.5)
    sys.exit(3)
if mode == "portcrash":
    print("[Error] Failed to bind to 0.0.0.0:42420 - Only one usage of each socket address "
          "(protocol/network address/port) is normally permitted", flush=True)
    time.sleep(0.3)
    sys.exit(1)
os.makedirs(os.path.join("Data", "Saves"), exist_ok=True)
os.makedirs(os.path.join("Data", "Logs"), exist_ok=True)
with open(os.path.join("Data", "Saves", "world.txt"), "w") as f:
    f.write("world")
children = []
for raw in sys.stdin:
    line = raw.strip()
    print("got:", line, flush=True)
    if line == "join":
        with open(os.path.join("Data", "Logs", "server-main.log"), "a") as f:
            f.write("[Server Event] Bob 1.2.3.4:5678 joins.\n")
    elif line == "leave":
        with open(os.path.join("Data", "Logs", "server-main.log"), "a") as f:
            f.write("[Server Event] Bob left.\n")
    elif line == "spawn":
        children.append(subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"]))
        with open("child.pid", "w") as f:
            f.write(str(children[-1].pid))
    elif line in ("/stop", "quit", "stop") and mode != "ignore":
        time.sleep(0.5)
        print("bye", flush=True)
        sys.exit(0)
# stdin closed (owner went away): behave like a server that keeps running
while mode == "ignore":
    time.sleep(1)

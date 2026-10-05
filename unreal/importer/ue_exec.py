"""Run Python inside a running Unreal Editor (Python remote execution, enabled in Config/DefaultEditor.ini) and print the result.

    python ue_exec.py "print(unreal.SystemLibrary.get_engine_version())"
    python ue_exec.py --file path/to/script.py
"""

import sys
import time

sys.path.insert(0, r"C:\Program Files\Epic Games\UE_5.8\Engine\Plugins\Experimental\PythonScriptPlugin\Content\Python")
import remote_execution as rx  # noqa: E402


def main() -> None:
    args = sys.argv[1:]
    if args[:1] == ["--file"]:
        command, mode = args[1], rx.MODE_EXEC_FILE
    else:
        command, mode = "\n".join(args), rx.MODE_EXEC_FILE
        # exec-file mode also runs a statement block, with its prints captured
    cfg = rx.RemoteExecutionConfig()
    cfg.multicast_bind_address = "127.0.0.1"
    r = rx.RemoteExecution(cfg)
    r.start()
    try:
        for _ in range(50):
            if r.remote_nodes:
                break
            time.sleep(0.2)
        if not r.remote_nodes:
            sys.exit("no Unreal Editor answering (is it running with remote execution on?)")
        r.open_command_connection(r.remote_nodes[0]["node_id"])
        res = r.run_command(command, unattended=True, exec_mode=mode)
        for line in res.get("output", []):
            print(line.get("output", "").rstrip())
        if not res.get("success"):
            print(res.get("result", ""))
            sys.exit(1)
    finally:
        r.stop()


if __name__ == "__main__":
    main()

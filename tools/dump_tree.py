"""Dump the raw window manager IPC tree to a JSON file.

Used to record fixtures for testing providers without a running compositor.
"""

import json
import sys

import i3ipc


def main():
    if len(sys.argv) != 2:
        print("usage: python tools/dump_tree.py OUTPUT.json")
        sys.exit(1)

    output_path = sys.argv[1]

    conn = i3ipc.Connection()
    tree = conn.get_tree()

    with open(output_path, "w") as f:
        json.dump(tree.ipc_data, f, indent=2)

    print(f"wrote {output_path}")


if __name__ == "__main__":
    main()

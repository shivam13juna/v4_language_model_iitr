#!/usr/bin/env python
"""Run this BEFORE regenerating a notebook from its build script.

    python _check_before_rebuild.py v6_workshop_1_build_nb.py v6_workshop_1_shipping_an_agent.ipynb

The build script is the master copy for everything IT wrote. Anything hand-added to the .ipynb
afterwards — a pasted mermaid diagram, an edited paragraph — exists nowhere else, and running
the build script would overwrite it silently. This builds the script into a throwaway
directory and compares cell sources both ways:

  * "in the notebook but not in the build script"  -> hand edits; fold them into the script
                                                      before rebuilding, or you lose them
  * "in the build script but not in the notebook"  -> the notebook is stale; a rebuild is due

When both sections report nothing, the two are in sync and rebuilding is safe.
"""
import os
import shutil
import subprocess
import sys
import tempfile

import nbformat


def normalise(source):
    """Compare on content, ignoring trailing whitespace — outputs and metadata are irrelevant."""
    return "\n".join(line.rstrip() for line in source.strip().splitlines())


def main(build_script, notebook_name):
    folder = os.path.dirname(os.path.abspath(__file__))

    # Build into a temp directory so the real notebook is never touched.
    temp_directory = tempfile.mkdtemp(prefix="rebuild_check_")
    try:
        shutil.copy(os.path.join(folder, build_script), temp_directory)
        subprocess.run([sys.executable, build_script], cwd=temp_directory,
                       check=True, stdout=subprocess.DEVNULL)

        live = nbformat.read(os.path.join(folder, notebook_name), as_version=4)
        fresh = nbformat.read(os.path.join(temp_directory, notebook_name), as_version=4)
    finally:
        shutil.rmtree(temp_directory, ignore_errors=True)

    fresh_sources = {normalise(cell.source) for cell in fresh.cells}
    live_sources = {normalise(cell.source) for cell in live.cells}

    print(f"live  : {len(live.cells)} cells   ({notebook_name})")
    print(f"fresh : {len(fresh.cells)} cells   (from {build_script})\n")

    hand_edits = [(index, cell) for index, cell in enumerate(live.cells)
                  if normalise(cell.source) not in fresh_sources]
    print("=== IN THE NOTEBOOK BUT NOT IN THE BUILD SCRIPT — a rebuild would lose these ===")
    for index, cell in hand_edits:
        print(f"\n--- live cell {index} [{cell.cell_type}] ---\n{cell.source}")
    if not hand_edits:
        print("(nothing)")

    stale = [(index, cell) for index, cell in enumerate(fresh.cells)
             if normalise(cell.source) not in live_sources]
    print("\n=== IN THE BUILD SCRIPT BUT NOT IN THE NOTEBOOK — the notebook is stale ===")
    for index, cell in stale:
        first_line = cell.source.strip().splitlines()[0][:80] if cell.source.strip() else ""
        print(f"  fresh cell {index} [{cell.cell_type}]: {first_line}")
    if not stale:
        print("(nothing)")

    if not hand_edits and not stale:
        print("\n✅ in sync — safe to rebuild")
    elif hand_edits:
        print(f"\n⚠️  {len(hand_edits)} cell(s) exist only in the notebook. "
              "Fold them into the build script first.")
    return 1 if hand_edits else 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2]))

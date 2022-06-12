import os
import shutil
import subprocess

os.chdir(os.path.dirname(__file__))
data_dir = os.path.abspath(os.path.join("..", "simplebot_mastodon", "data"))

if __name__ == "__main__":
    subprocess.check_call(["pnpm", "i"])
    subprocess.check_call(["pnpm", "build"])

    shutil.copytree("dist", data_dir, dirs_exist_ok=True)
    shutil.copy("icon.png", data_dir)
    shutil.copy("icons.svg", data_dir)

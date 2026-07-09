from setuptools import setup

package_name = "patrol_security"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools", "opencv-python-headless", "numpy", "paho-mqtt", "requests"],
    zip_safe=True,
    maintainer="rock",
    maintainer_email="rock@todo.todo",
    description="P1c patrol vision and track assist",
    license="MIT",
    entry_points={
        "console_scripts": [
            "patrol_vision_node = patrol_security.patrol_vision_node:main",
            "patrol_track_assist = patrol_security.patrol_track_assist_node:main",
        ],
    },
)

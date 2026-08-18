"""Load complete, self-contained Demo3 robot packages."""

from .robot_package import (
    RobotPackage,
    RobotPackageError,
    load_indexed_robot_package,
    load_robot_package,
)

__all__ = [
    "RobotPackage",
    "RobotPackageError",
    "load_indexed_robot_package",
    "load_robot_package",
]

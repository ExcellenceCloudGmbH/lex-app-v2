import os
import shutil
import sys
from pathlib import Path

from setuptools import setup, find_packages
from setuptools.command.install import install

with open('requirements.txt') as f:
    install_requires = f.read().splitlines()


class CustomInstallCommand(install):
    def run(self):
        # First, run the standard installation
        install.run(self)

        # Now handle the custom installation of other_directory
        self.move_other_directory()

        # Fix IntelliJ linter by adding parent directory to Python path
        self.fix_intellij_linter()

    def move_other_directory(self):
        # Define the source and target paths
        source = os.path.join(os.path.dirname(__file__), 'lex', 'generic_app')
        target = os.path.join(os.path.dirname(self.install_lib), 'generic_app')

        # Ensure the package_data entry points to the correct location
        if os.path.exists(target):
            shutil.rmtree(target)  # Remove the existing directory if it exists
        shutil.move(source, target)
        print(f'Moved other_directory to {target}')

    def fix_intellij_linter(self):
        """
        Automatically fix IntelliJ linter by adding the parent directory
        to the virtual environment's Python path via a .pth file.
        """
        try:
            # Find the actual site-packages directory (works for venv, .venv, etc.)
            import site
            site_packages = None

            # Method 1: Check if we're in a virtual environment
            if hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
                # We're in a virtual environment
                for path in site.getsitepackages():
                    if 'site-packages' in path and (
                            sys.prefix in path or getattr(sys, 'real_prefix', sys.prefix) in path):
                        site_packages = Path(path)
                        break

                # Fallback: construct the path manually
                if not site_packages:
                    python_version = f"python{sys.version_info.major}.{sys.version_info.minor}"
                    site_packages = Path(sys.prefix) / "lib" / python_version / "site-packages"

            # Method 2: Fallback to user site-packages or global
            if not site_packages or not site_packages.exists():
                site_packages = Path(site.getsitepackages()[0]) if site.getsitepackages() else Path(
                    site.getusersitepackages())

            print(f"📍 Target site-packages: {site_packages}")

            # Find the project root by looking for the parent directory
            # that contains the main package (assumes package name matches directory name)
            current_path = Path.cwd()
            project_root = None

            # Method 1: Look for a directory with the same name as our package
            package_name = None
            for pkg in find_packages():
                if not '.' in pkg:  # Top-level package
                    package_name = pkg
                    break

            if package_name:
                # Walk up the directory tree to find where the package directory is
                test_path = current_path
                while test_path != test_path.parent:  # Stop at filesystem root
                    package_dir = test_path / package_name
                    if package_dir.exists() and (package_dir / '__init__.py').exists():
                        project_root = test_path
                        break
                    test_path = test_path.parent

            # Method 2: Fallback - use current working directory's parent
            if not project_root:
                project_root = current_path.parent

            print(f"📁 Project root detected: {project_root}")

            # Create .pth file in site-packages
            pth_filename = f"{package_name}_intellij_fix.pth" if package_name else "intellij_fix.pth"
            pth_file = site_packages / pth_filename

            # Ensure site-packages directory exists
            site_packages.mkdir(parents=True, exist_ok=True)

            with open(pth_file, 'w') as f:
                f.write(str(project_root) + '\n')

            print(f"✓ IntelliJ linter fix applied!")
            print(f"  Added {project_root} to Python path")
            print(f"  Created: {pth_file}")
            print(f"  Virtual environment: {sys.prefix}")
            print(f"  This enables imports like: from {package_name}.module import Class")

        except Exception as e:
            print(f"⚠ Warning: Could not apply IntelliJ linter fix: {e}")
            print(f"  Current Python: {sys.executable}")
            print(f"  Python prefix: {sys.prefix}")
            print("  You may need to manually configure your IDE's Python path")


setup(
    name="lex-app",
    version="1.0.6",
    author="Melih Sünbül",
    author_email="m.sunbul@lund-it.com",
    description="A Python / Django library to create business applications easily with complex logic",
    long_description_content_type="text/markdown",
    url="https://github.com/LundIT/lex-app",
    packages=find_packages(),
    include_package_data=True,
    entry_points={
        'console_scripts': [
            'lex = lex.__main__:main',
        ]
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    install_requires=install_requires,
    python_requires='>=3.6',
    cmdclass={
        'install': CustomInstallCommand,
    },
)

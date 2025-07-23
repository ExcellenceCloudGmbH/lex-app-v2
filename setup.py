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
        """Add project root to virtual environment's Python path via .pth file."""
        try:
            import site

            # Find virtual environment site-packages
            if hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
                python_version = f"python{sys.version_info.major}.{sys.version_info.minor}"
                site_packages = Path(sys.prefix) / "lib" / python_version / "site-packages"
            else:
                site_packages_list = site.getsitepackages() if hasattr(site, 'getsitepackages') else []
                if site_packages_list:
                    site_packages = Path(site_packages_list[0])
                else:
                    site_packages = Path(site.getusersitepackages())

            print(f"🎯 Target site-packages: {site_packages}")

            # Find the actual project root by looking outside the virtual environment
            # The virtual environment is usually inside the project or the project is the parent
            venv_path = Path(sys.prefix)
            package_name = next((pkg for pkg in find_packages() if '.' not in pkg), 'project')

            print(f"📦 Package name: {package_name}")
            print(f"🔍 Virtual env path: {venv_path}")

            # Method 1: Check if venv is inside project (most common case)
            project_root = None
            test_path = venv_path.parent

            # Look for the project directory that contains both the venv and the package
            while test_path != test_path.parent and not project_root:
                # Check if this directory contains the package
                if (test_path / package_name).exists() and (test_path / package_name / '__init__.py').exists():
                    project_root = test_path
                    break
                test_path = test_path.parent

            # Method 2: If venv name suggests it's inside project (e.g., .venv, venv)
            if not project_root and venv_path.name in ['.venv', 'venv', 'env']:
                potential_root = venv_path.parent
                if (potential_root / package_name).exists() and (
                        potential_root / package_name / '__init__.py').exists():
                    project_root = potential_root

            # Method 3: Fallback - use the parent of venv
            if not project_root:
                project_root = venv_path.parent

            print(f"📁 Detected project root: {project_root}")

            # Ensure site-packages directory exists
            if not site_packages.exists():
                print(f"⚠ Site-packages doesn't exist: {site_packages}")
                return

            # Create .pth file
            pth_file = site_packages / f"{package_name}_intellij_fix.pth"

            with open(pth_file, 'w') as f:
                f.write(str(project_root) + '\n')

            print(f"✅ IntelliJ linter fix applied!")
            print(f"   Added: {project_root}")
            print(f"   To: {pth_file}")

        except Exception as e:
            import traceback
            print(f"❌ IntelliJ linter fix failed: {e}")
            print(f"   Traceback: {traceback.format_exc()}")


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

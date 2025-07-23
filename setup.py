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

        # Custom installation
        if os.path.exists(os.path.join(os.path.dirname(__file__), 'lex', 'generic_app')):
            self.move_other_directory()

        self.fix_intellij_linter()

    def move_other_directory(self):
        # Define the source and target paths
        source = os.path.join(os.path.dirname(__file__), 'lex', 'generic_app')
        target = os.path.join(os.path.dirname(self.install_lib), 'generic_app')

        if os.path.exists(source):
            if os.path.exists(target):
                shutil.rmtree(target)
            shutil.move(source, target)
            print(f'Moved other_directory to {target}')

    def fix_intellij_linter(self):
        """Add project root to virtual environment's Python path via .pth file."""
        try:
            import site

            if hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
                python_version = f"python{sys.version_info.major}.{sys.version_info.minor}"
                site_packages = Path(sys.prefix) / "lib" / python_version / "site-packages"
            else:
                site_packages = Path(site.getsitepackages()[0]) if site.getsitepackages() else Path(site.getusersitepackages())

            current_path = Path.cwd()
            package_name = next((pkg for pkg in find_packages() if '.' not in pkg), None)

            project_root = current_path.parent
            if package_name:
                test_path = current_path
                while test_path != test_path.parent:
                    if (test_path / package_name).exists() and (test_path / package_name / '__init__.py').exists():
                        project_root = test_path
                        break
                    test_path = test_path.parent

            pth_file = site_packages / f"{package_name or 'project'}_intellij_fix.pth"
            site_packages.mkdir(parents=True, exist_ok=True)

            with open(pth_file, 'w') as f:
                f.write(str(project_root) + '\n')

            print(f"✓ IntelliJ linter fix: Added {project_root} to {pth_file}")

        except Exception as e:
            print(f"⚠ IntelliJ linter fix failed: {e}")

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
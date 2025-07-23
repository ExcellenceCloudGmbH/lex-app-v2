import os
import shutil
import sys
import xml.etree.ElementTree as ET
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

        # Fix IntelliJ linter across all platforms
        self.fix_intellij_linter()

    def move_other_directory(self):
        # Define the source and target paths
        source = os.path.join(os.path.dirname(__file__), 'lex', 'generic_app')
        target = os.path.join(os.path.dirname(self.install_lib), 'generic_app')

        # Only move if source exists
        if os.path.exists(source):
            # Ensure the package_data entry points to the correct location
            if os.path.exists(target):
                shutil.rmtree(target)  # Remove the existing directory if it exists
            shutil.move(source, target)
            print(f'✅ Moved other_directory to {target}')
        else:
            print(f'⚠ Source directory not found: {source} - skipping move')

    def fix_intellij_linter(self):
        """Cross-platform IntelliJ linter fix."""
        try:
            print("🔧 Applying IntelliJ linter fix...")

            # Detect platform
            platform = self._detect_platform()
            print(f"📍 Platform: {platform}")

            # Find project structure
            project_root, venv_path = self._find_project_structure()
            package_name = self._get_package_name()

            print(f"📦 Package: {package_name}")
            print(f"📁 Project root: {project_root}")
            print(f"🐍 Virtual env: {venv_path}")

            # Update IntelliJ configuration (for static analysis)
            if self._update_intellij_config(project_root, venv_path, platform):
                print("✅ IntelliJ linter fix applied!")
            else:
                print("⚠ IntelliJ linter fix could not be applied - no matching configuration found")
                print("  ℹ️ Tip: Make sure PyCharm is using the correct virtual environment")

        except Exception as e:
            print(f"⚠ IntelliJ linter fix failed: {e}")

    def _detect_platform(self):
        """Detect the current platform."""
        if os.name == 'nt':
            return 'windows'
        elif sys.platform == 'darwin':
            return 'macos'
        else:
            return 'linux'

    def _get_package_name(self):
        """Get the main package name."""
        packages = find_packages()
        return next((pkg for pkg in packages if '.' not in pkg), 'project')

    def _find_project_structure(self):
        """Find project root and virtual environment."""
        venv_path = Path(sys.prefix)

        # Find project root - look for the directory containing the main package
        package_name = self._get_package_name()

        # Walk up from venv to find project root
        test_path = venv_path.parent
        project_root = None

        while test_path != test_path.parent:
            if (test_path / package_name).exists() and (test_path / package_name / '__init__.py').exists():
                project_root = test_path
                break
            test_path = test_path.parent

        # Fallback methods
        if not project_root:
            if venv_path.name in ['.venv', 'venv', 'env']:
                potential_root = venv_path.parent
                if (potential_root / package_name).exists():
                    project_root = potential_root

        if not project_root:
            project_root = venv_path.parent

        return str(project_root), str(venv_path)

    def _update_intellij_config(self, project_root, venv_path, platform):
        """Update IntelliJ configuration files."""
        try:
            config_dirs = self._find_intellij_config_dirs(platform)

            if not config_dirs:
                print("  ⚠ No IntelliJ configuration found")
                return False

            updated_any = False
            for config_dir in config_dirs:
                jdk_table_path = config_dir / "options" / "jdk.table.xml"
                if jdk_table_path.exists():
                    if self._update_jdk_table(jdk_table_path, project_root, venv_path, platform):
                        print(f"  ✅ Updated IntelliJ config: {config_dir.name}")
                        updated_any = True

            if not updated_any:
                print("  ⚠ No matching interpreter found in IntelliJ configuration")

            return updated_any

        except Exception as e:
            print(f"  ⚠ Could not update IntelliJ config: {e}")
            return False

    def _find_intellij_config_dirs(self, platform):
        """Find IntelliJ configuration directories across platforms."""
        home = Path.home()
        config_dirs = []

        if platform == 'linux':
            # Linux: ~/.config/JetBrains/
            linux_config = home / ".config" / "JetBrains"
            if linux_config.exists():
                config_dirs.extend(linux_config.glob("*PyCharm*"))
                config_dirs.extend(linux_config.glob("*IntelliJ*"))

        elif platform == 'macos':
            # macOS: ~/Library/Application Support/JetBrains/
            macos_config = home / "Library" / "Application Support" / "JetBrains"
            if macos_config.exists():
                config_dirs.extend(macos_config.glob("*PyCharm*"))
                config_dirs.extend(macos_config.glob("*IntelliJ*"))

        elif platform == 'windows':
            # Windows: %APPDATA%\JetBrains\
            appdata = Path(os.environ.get('APPDATA', str(home / 'AppData' / 'Roaming')))
            windows_config = appdata / "JetBrains"
            if windows_config.exists():
                config_dirs.extend(windows_config.glob("*PyCharm*"))
                config_dirs.extend(windows_config.glob("*IntelliJ*"))

        return [d for d in config_dirs if d.is_dir()]

    def _update_jdk_table(self, jdk_table_path, project_root, venv_path, platform):
        """Update jdk.table.xml with project root path."""
        try:
            # Parse XML
            tree = ET.parse(str(jdk_table_path))
            root = tree.getroot()

            # Convert paths to IntelliJ format
            home_str = str(Path.home())
            if platform == 'windows':
                # Windows uses forward slashes in IntelliJ paths
                project_root_relative = project_root.replace(home_str, '').replace('\\', '/')
                venv_path_search = venv_path.replace('\\', '/')
            else:
                project_root_relative = project_root.replace(home_str, '')
                venv_path_search = venv_path

            intellij_path = f"file://$USER_HOME${project_root_relative}"

            # Find interpreter that matches our virtual environment
            updated = False
            print(f"  🔍 Looking for venv path containing: {venv_path_search}")

            for jdk in root.findall(".//jdk"):
                home_path = jdk.find("homePath")
                name = jdk.find("name")

                if home_path is not None and name is not None:
                    home_value = home_path.get("value", "")
                    name_value = name.get("value", "")

                    print(f"  👀 Checking interpreter: {name_value}")
                    print(f"      Path: {home_value}")

                    # Match any interpreter that uses our virtual environment
                    if venv_path_search in home_value:

                        print(f"  ✅ Found matching interpreter: {name_value}")

                        # Add to classPath
                        class_path = jdk.find(".//classPath/root[@type='composite']")
                        if class_path is not None:
                            # Check if already exists
                            exists = any(
                                r.get("url") == intellij_path for r in class_path.findall("root[@type='simple']"))

                            if not exists:
                                new_root = ET.SubElement(class_path, "root")
                                new_root.set("url", intellij_path)
                                new_root.set("type", "simple")
                                print(f"  📝 Added path to classPath: {intellij_path}")
                                updated = True
                            else:
                                print(f"  ℹ️ Path already exists in classPath: {intellij_path}")
                                updated = True  # Consider it updated if path already exists

            if updated:
                # Save with proper formatting
                tree.write(str(jdk_table_path), encoding='utf-8', xml_declaration=True)
                return True

            return False

        except Exception as e:
            print(f"    ⚠ Error updating XML: {e}")
            return False


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

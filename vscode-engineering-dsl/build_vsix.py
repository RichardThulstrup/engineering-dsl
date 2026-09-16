"""Create an offline VSIX from the extension's dependency-free source files."""
import json
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parent
package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
output = ROOT / "dist" / f"engineering-dsl-{package['version']}.vsix"
output.parent.mkdir(exist_ok=True)
for required in ["src/extension.js", "src/client.js", "python/checker.py",
                 "syntaxes/engineering-dsl.tmLanguage.json", "syntaxes/injection.tmLanguage.json"]:
    if not (ROOT / required).is_file():
        raise SystemExit(f"Missing {required}; run generate_grammar.py before packaging")
manifest = f'''<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011" xmlns:d="http://schemas.microsoft.com/developer/vsx-schema-design/2011">
  <Metadata>
    <Identity Language="en-US" Id="{package['name']}" Version="{package['version']}" Publisher="{package['publisher']}"/>
    <DisplayName>{escape(package['displayName'])}</DisplayName>
    <Description xml:space="preserve">{escape(package['description'])}</Description>
    <Categories>Programming Languages,Linters,Snippets</Categories>
    <Properties>
      <Property Id="Microsoft.VisualStudio.Code.Engine" Value="{escape(package['engines']['vscode'])}"/>
      <Property Id="Microsoft.VisualStudio.Code.ExtensionDependencies" Value="{','.join(package['extensionDependencies'])}"/>
      <Property Id="Microsoft.VisualStudio.Code.ExtensionKind" Value="workspace"/>
    </Properties>
  </Metadata>
  <Installation><InstallationTarget Id="Microsoft.VisualStudio.Code"/></Installation>
  <Dependencies/>
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true"/>
    <Asset Type="Microsoft.VisualStudio.Services.Content.Details" Path="extension/README.md" Addressable="true"/>
    <Asset Type="Microsoft.VisualStudio.Services.Content.License" Path="extension/LICENSE" Addressable="true"/>
  </Assets>
</PackageManifest>'''
content_types = '''<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="json" ContentType="application/json"/>
<Default Extension="js" ContentType="application/javascript"/>
<Default Extension="py" ContentType="text/plain"/>
<Default Extension="md" ContentType="text/markdown"/>
<Default Extension="vsixmanifest" ContentType="text/xml"/>
<Override PartName="/extension/LICENSE" ContentType="text/plain"/>
</Types>'''
with ZipFile(output, "w", ZIP_DEFLATED) as archive:
    archive.writestr("extension.vsixmanifest", manifest)
    archive.writestr("[Content_Types].xml", content_types)
    for name in ["package.json", "language-configuration.json", "symbols.json", "README.md", "LICENSE"]:
        archive.write(ROOT / name, f"extension/{name}")
    for folder, suffix in [("src", ".js"), ("python", ".py"), ("syntaxes", ".json")]:
        for file in sorted((ROOT / folder).glob(f"*{suffix}")):
            archive.write(file, "extension/" + file.relative_to(ROOT).as_posix())
print(output)

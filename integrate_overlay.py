"""
Integrate MoneyOverlay dylib into LiveContainer IPA.

Usage:
    python integrate_overlay.py <livecontainer.ipa> <dylib_path> [output.ipa]

Steps:
  1. Extracts LiveContainer IPA
  2. Copies libMoneyOverlay.dylib into Payload/*.app/Frameworks/
  3. Signs dylib with ldid (using existing app entitlements)
  4. Adds @rpath/libMoneyOverlay.dylib to preloadLibraries.txt
  5. Repacks IPA
"""

import zipfile
import sys
import os
import shutil
import subprocess
import tempfile
import json

def find_app_dir(payload_dir):
    """Find the .app directory in Payload."""
    for entry in os.listdir(payload_dir):
        if entry.endswith('.app'):
            return os.path.join(payload_dir, entry)
    return None

def get_entitlements(app_dir):
    """Extract entitlements from embedded.mobileprovision or existing binary."""
    # Try to get entitlements from existing binary
    binary_path = None
    for entry in os.listdir(app_dir):
        if entry.endswith('.app'):
            # The binary name matches the .app name without extension
            binary_path = os.path.join(app_dir, entry)
            break
    
    if not binary_path:
        # Try the app directory name (minus .app)
        app_name = os.path.basename(app_dir)[:-4]
        binary_path = os.path.join(app_dir, app_name)
    
    if os.path.exists(binary_path):
        try:
            result = subprocess.run(
                ['ldid', '-e', binary_path],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
    
    # Default entitlements for sideloading
    return '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>get-task-allow</key>
    <true/>
    <key>com.apple.security.cs.disable-library-validation</key>
    <true/>
</dict>
</plist>'''

def integrate(ipa_path, dylib_path, output_path=None):
    if not os.path.exists(ipa_path):
        print(f"Error: IPA not found: {ipa_path}")
        return False
    if not os.path.exists(dylib_path):
        print(f"Error: dylib not found: {dylib_path}")
        return False
    
    tmpdir = tempfile.mkdtemp()
    try:
        # Extract IPA
        print(f"Extracting {ipa_path}...")
        with zipfile.ZipFile(ipa_path, 'r') as z:
            z.extractall(tmpdir)
        
        payload_dir = os.path.join(tmpdir, 'Payload')
        app_dir = find_app_dir(payload_dir)
        if not app_dir:
            print("Error: No .app directory found in Payload")
            return False
        
        print(f"Found app: {app_dir}")
        
        # Create Frameworks directory
        frameworks_dir = os.path.join(app_dir, 'Frameworks')
        os.makedirs(frameworks_dir, exist_ok=True)
        
        # Copy dylib
        dylib_dest = os.path.join(frameworks_dir, 'libMoneyOverlay.dylib')
        shutil.copy2(dylib_path, dylib_dest)
        print(f"Copied dylib to {dylib_dest}")
        
        # Sign dylib with entitlements
        print("Signing dylib...")
        entitlements = get_entitlements(app_dir)
        ent_file = os.path.join(tmpdir, 'entitlements.plist')
        with open(ent_file, 'w') as f:
            f.write(entitlements)
        
        try:
            subprocess.run(
                ['ldid', f'-S{ent_file}', dylib_dest],
                check=True, timeout=30
            )
            print("Dylib signed successfully")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"Warning: ldid signing failed ({e}), continuing anyway")
        
        # Update preloadLibraries.txt
        preload_path = os.path.join(app_dir, 'preloadLibraries.txt')
        entry = '@rpath/libMoneyOverlay.dylib\n'
        if os.path.exists(preload_path):
            with open(preload_path, 'r') as f:
                existing = f.read()
            if entry not in existing:
                with open(preload_path, 'a') as f:
                    f.write(entry)
                print(f"Appended to {preload_path}")
        else:
            with open(preload_path, 'w') as f:
                f.write(entry)
            print(f"Created {preload_path}")
        
        # Repack IPA
        if not output_path:
            base = os.path.splitext(os.path.basename(ipa_path))[0]
            output_path = os.path.join(
                os.path.dirname(ipa_path),
                f'{base}_with_overlay.ipa'
            )
        
        print(f"Creating IPA: {output_path}")
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zout:
            for root, _, files in os.walk(tmpdir):
                for file in files:
                    if file == entitlements:
                        continue
                    filepath = os.path.join(root, file)
                    arcname = os.path.relpath(filepath, tmpdir)
                    zout.write(filepath, arcname)
        
        print(f"Done! Created {output_path}")
        return True
        
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python integrate_overlay.py <livecontainer.ipa> <dylib_path> [output.ipa]")
        sys.exit(1)
    
    ipa_path = sys.argv[1]
    dylib_path = sys.argv[2]
    output_path = sys.argv[3] if len(sys.argv) > 3 else None
    
    success = integrate(ipa_path, dylib_path, output_path)
    sys.exit(0 if success else 1)

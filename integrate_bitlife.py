"""
Injects libMoneyOverlay.dylib directly into BitLife cracked IPA.

Usage:
    python integrate_bitlife.py <bitlife_v15.ipa> <dylib_path> [output.ipa]

Steps:
  1. Extracts IPA
  2. Copies dylib to Payload/*.app/Frameworks/libMoneyOverlay.dylib
  3. Patches the main Mach-O binary to load the dylib (adds LC_LOAD_DYLIB)
  4. Signs with ldid
  5. Repacks IPA
"""

import zipfile
import sys
import os
import shutil
import subprocess
import tempfile
import struct
import plistlib

def align(x, a):
    return (x + a - 1) & ~(a - 1)

def find_app_dir(payload_dir):
    for entry in os.listdir(payload_dir):
        if entry.endswith('.app'):
            return os.path.join(payload_dir, entry)
    return None

def find_main_binary(app_dir):
    plist_path = os.path.join(app_dir, 'Info.plist')
    if os.path.exists(plist_path):
        with open(plist_path, 'rb') as f:
            plist = plistlib.load(f)
        executable = plist.get('CFBundleExecutable')
        if executable:
            path = os.path.join(app_dir, executable)
            if os.path.exists(path):
                return path
    name = os.path.basename(app_dir)[:-4]
    path = os.path.join(app_dir, name)
    return path if os.path.exists(path) else None

def macho_add_load_dylib(binary_path, dylib_path):
    """
    Adds an LC_LOAD_DYLIB command to a Mach-O 64-bit binary.
    Injects the command in the gap between last load command and first section.
    """
    with open(binary_path, 'rb') as f:
        data = bytearray(f.read())

    magic = struct.unpack_from('<I', data, 0)[0]
    MH_MAGIC_64 = 0xFEEDFACF
    if magic != MH_MAGIC_64:
        print(f"Error: not a 64-bit Mach-O (magic=0x{magic:08X})")
        return False

    header = struct.unpack_from('<IIIIIIII', data, 0)
    cputype, cpusubtype, filetype, ncmds, sizeofcmds, flags, reserved = header[1:]
    load_cmds_end = 32 + sizeofcmds
    print(f"Mach-O: {ncmds} load commands, {sizeofcmds} bytes, end=0x{load_cmds_end:X}")

    # Find the first section within __TEXT to get usable gap
    offset = 32
    first_section_offset = None
    for i in range(ncmds):
        cmd, cmdsize = struct.unpack_from('<II', data, offset)
        if cmd == 0x19:  # LC_SEGMENT_64
            nsects = struct.unpack_from('<I', data, offset+64)[0]
            if nsects > 0:
                sect_off = offset + 72
                soff = struct.unpack_from('<I', data, sect_off+48)[0]
                first_section_offset = soff
                break
        offset += cmdsize

    if first_section_offset is None or first_section_offset <= load_cmds_end:
        print(f"Error: no gap available (first section at 0x{first_section_offset:X}, load end 0x{load_cmds_end:X})")
        return False

    gap_size = first_section_offset - load_cmds_end
    print(f"Gap: {gap_size} bytes (0x{load_cmds_end:X} - 0x{first_section_offset:X})")

    dylib_path_bytes = dylib_path.encode('utf-8') + b'\x00'
    dylib_path_padded = dylib_path_bytes + b'\x00' * (align(len(dylib_path_bytes), 8) - len(dylib_path_bytes))
    
    # dylib_command structure:
    #   cmd (uint32) = 0x1C (LC_LOAD_WEAK_DYLIB - won't crash app if dylib fails)
    #   cmdsize (uint32) = total size
    #   dylib.offset (uint32) = offset from start of cmd to path
    #   dylib.timestamp (uint32) = 0
    #   dylib.current_version (uint32) = 0
    #   dylib.compat_version (uint32) = 0
    #   path (null-terminated, padded to 8 bytes)
    
    dylib_off = 24  # offset from start of cmd to path component
    cmd_size = dylib_off + len(dylib_path_padded)
    
    cmd_data = struct.pack('<II', 0x1C, cmd_size)  # LC_LOAD_WEAK_DYLIB
    cmd_data += struct.pack('<IIII', dylib_off, 0, 0, 0)
    cmd_data += dylib_path_padded
    
    if len(cmd_data) > gap_size:
        print(f"Error: command size ({len(cmd_data)}) exceeds gap ({gap_size})")
        return False

    # Insert the command after the last load command
    insert_pos = load_cmds_end
    data[insert_pos:insert_pos + len(cmd_data)] = cmd_data
    
    # Update header
    new_ncmds = ncmds + 1
    new_sizeofcmds = sizeofcmds + len(cmd_data)
    struct.pack_into('<IIIIIIII', data, 0,
                     MH_MAGIC_64, cputype, cpusubtype, filetype,
                     new_ncmds, new_sizeofcmds, flags, reserved)
    
    print(f"Added LC_LOAD_WEAK_DYLIB: {dylib_path}")
    print(f"  ncmds: {ncmds} -> {new_ncmds}")
    print(f"  sizeofcmds: {sizeofcmds} -> {new_sizeofcmds}")

    # Write back
    with open(binary_path, 'wb') as f:
        f.write(data)
    return True

def get_entitlements(app_dir):
    binary_path = find_main_binary(app_dir)
    if binary_path and os.path.exists(binary_path):
        try:
            result = subprocess.run(
                ['ldid', '-e', binary_path],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
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
        print(f"Extracting {ipa_path}...")
        with zipfile.ZipFile(ipa_path, 'r') as z:
            z.extractall(tmpdir)

        payload_dir = os.path.join(tmpdir, 'Payload')
        app_dir = find_app_dir(payload_dir)
        if not app_dir:
            print("Error: No .app directory in Payload")
            return False

        app_name = os.path.basename(app_dir)
        print(f"App: {app_name}")

        # Create Frameworks directory
        frameworks_dir = os.path.join(app_dir, 'Frameworks')
        os.makedirs(frameworks_dir, exist_ok=True)

        # Copy dylib
        dylib_dest = os.path.join(frameworks_dir, 'libMoneyOverlay.dylib')
        shutil.copy2(dylib_path, dylib_dest)
        print(f"Copied dylib to {dylib_dest}")

        # Patch main binary
        binary_path = find_main_binary(app_dir)
        if not binary_path:
            print("Error: main binary not found")
            return False

        print(f"Patching binary: {binary_path}")
        rpath_path = '@executable_path/Frameworks/libMoneyOverlay.dylib'
        if macho_add_load_dylib(binary_path, rpath_path):
            print("Binary patched successfully")
        else:
            print("Error: failed to patch binary")
            return False

        # Sign everything with ldid
        print("Signing with ldid...")
        entitlements = get_entitlements(app_dir)
        ent_file = os.path.join(tmpdir, 'entitlements.plist')
        with open(ent_file, 'w') as f:
            f.write(entitlements)

        try:
            subprocess.run(['ldid', '-S' + ent_file, dylib_dest], check=True, timeout=30)
            print("Dylib signed")
            subprocess.run(['ldid', '-S' + ent_file, binary_path], check=True, timeout=30)
            print("Binary re-signed")
            # Sign all frameworks
            for root, _, files in os.walk(frameworks_dir):
                for file in files:
                    fp = os.path.join(root, file)
                    if os.path.isfile(fp) and not file.endswith('.txt') and not file.endswith('.plist'):
                        try:
                            subprocess.run(['ldid', '-S' + ent_file, fp], check=True, timeout=30)
                        except:
                            pass
            print("All binaries signed")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"Warning: ldid signing failed ({e}), continuing anyway")

        # Repack IPA
        if not output_path:
            base = os.path.splitext(os.path.basename(ipa_path))[0]
            output_path = os.path.join(
                os.path.dirname(ipa_path),
                f'{base}_overlay.ipa'
            )
        
        # Remove old payload if exists
        if os.path.exists(output_path):
            os.remove(output_path)

        print(f"Creating IPA: {output_path}")
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zout:
            for root, _, files in os.walk(tmpdir):
                for file in files:
                    filepath = os.path.join(root, file)
                    arcname = os.path.relpath(filepath, tmpdir)
                    if arcname.startswith('entitlements'):
                        continue
                    # Mach-O magic bytes for permission detection
                    is_macho = False
                    try:
                        with open(filepath, 'rb') as f:
                            magic = f.read(4)
                            is_macho = magic in (b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe',
                                                  b'\xfe\xed\xfa\xcf', b'\xcf\xfa\xed\xfe',
                                                  b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca')
                    except:
                        pass
                    perm = 0o755 if is_macho else 0o644
                    info = zipfile.ZipInfo(arcname)
                    info.external_attr = perm << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    with open(filepath, 'rb') as f:
                        zout.writestr(info, f.read())

        print(f"Done! Created {output_path}")
        return True

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python integrate_bitlife.py <bitlife_v15.ipa> <dylib_path> [output.ipa]")
        sys.exit(1)

    ipa_path = sys.argv[1]
    dylib_path = sys.argv[2]
    output_path = sys.argv[3] if len(sys.argv) > 3 else None

    success = integrate(ipa_path, dylib_path, output_path)
    sys.exit(0 if success else 1)

# kfxgen Plugin - Installation Guide

## Quick Start

### 1. Install the Plugin

1. **Download** the plugin file:
   ```
   kfxgen-plugin.zip
   ```

2. **Open Calibre**

3. **Go to Preferences → Plugins**
   - Menu: `Preferences` (or press `Ctrl+P` / `Cmd+,`)
   - Click `Plugins` in the left sidebar

4. **Load Plugin from File**
   - Click `Load plugin from file` button (bottom right)
   - Navigate to and select `kfxgen-plugin.zip`
   - Click `Open`

5. **Confirm Installation**
   - Calibre will ask: "Are you sure you want to proceed?"
   - Click `Yes`
   - You should see: "KFX Output (kfxgen)" in the File type plugins list

6. **Restart Calibre**
   - Close and reopen Calibre
   - The plugin is now active

### 2. Convert Your First Book

1. **Add a test book** to your library (or use an existing one)

2. **Convert to KFX**:
   - Select the book
   - Click `Convert books` button
   - In the output format dropdown (top right), select `KFX`
   - Click `OK`

3. **Check the output**:
   - After conversion completes, right-click the book
   - Choose `Open containing folder`
   - Find the `.kfx` file

### 3. Test on Kindle

1. **Transfer to Kindle**:
   ```
   Connect Kindle via USB
   Copy the .kfx file to: documents/ folder
   Safely eject Kindle
   ```

2. **Open on Kindle**:
   - The book should appear in your library
   - Tap to open
   - Content should display correctly

## Verification

### Plugin Installed Successfully

After installation, verify the plugin is active:

1. Go to `Preferences → Plugins`
2. Look for `KFX Output (kfxgen)` under "File type plugins"
3. It should show version `0.1.0`

### Conversion Working

When you convert a book to KFX:

1. Check the conversion log (click "Jobs" at bottom right)
2. You should see:
   ```
   kfxgen v0.1.0 - Starting conversion
   Extracting metadata...
     Title: [Your Book Title]
     Author: [Author Name]
   Extracting text content...
   Generating KFX file...
   ✓ KFX file generated successfully
   ```

### File Structure

A generated KFX file should be:
- **Size**: ~65-67 KB for minimal books
- **Extension**: `.kfx`
- **Can open in**: Hex editor to verify CONT header

## Troubleshooting

### Plugin Won't Install

**Error**: "Plugin is invalid"
- **Solution**: Make sure you downloaded `kfxgen-plugin.zip` (not extracted folder)
- **Solution**: Try downloading again (file may be corrupted)

**Error**: "Requires Calibre version 5.0+"
- **Solution**: Update Calibre to latest version
- Check version: `Help → About Calibre`

### Plugin Installed But Not Showing

- **Restart Calibre** (required after installation)
- Check under `Preferences → Plugins → File type plugins`
- Look for "KFX Output (kfxgen)"

### Conversion Fails

**Check the log**:
1. Click `Jobs` (bottom right in Calibre)
2. Double-click the failed conversion
3. Look for error messages

**Common issues**:
- **"Template file not found"**: Plugin installation incomplete, reinstall
- **"No attribute 'metadata'"**: Book has no metadata, add title/author
- **"Content extraction failed"**: Book format not supported, try converting to EPUB first

### Book Doesn't Appear on Kindle

1. **Check file extension**: Must be `.kfx` (not `.azw3` or `.mobi`)
2. **Check file size**: Should be ~65KB+
3. **Check Kindle model**: Kindle Paperwhite 3+ required
4. **Try the verified POC file**: Use `poc9_v5_generated.kfx` from research folder

### Content is Truncated

This is expected in v0.1.0:
- **Limit**: 500 characters per book
- **Why**: Template-based limitation
- **Workaround**: None currently
- **Future**: Will be fixed in v0.2.0

## Advanced Usage

### Converting Multiple Books

You can batch convert:

1. Select multiple books (Ctrl+Click or Shift+Click)
2. Click `Convert books`
3. Choose `KFX` as output format
4. Click `OK`

All books will be queued for conversion.

### Custom Conversion Settings

Currently available options:

1. Click `Convert books`
2. Select `KFX Output` in the left sidebar
3. Configure:
   - **Fast-Fonts Mode**: Keep ON for Fast-Fonts support
   - **Compression**: auto (recommended)
   - **Image Quality**: high (default)

### Using with Fast-Fonts

1. **Install Fast-Font on Kindle** (one-time setup):
   ```
   Connect Kindle via USB
   Create folder: /mnt/us/fonts/ (if not exists)
   Copy your .otf Fast-Font file there
   Safely eject
   ```

2. **Convert book** using this plugin (normal conversion)

3. **Select font on Kindle**:
   - Open the book
   - Tap top of screen → Aa → Fonts
   - Select your Fast-Font
   - Initial letters will automatically bold!

## Uninstallation

To remove the plugin:

1. Go to `Preferences → Plugins`
2. Find `KFX Output (kfxgen)` under File type plugins
3. Right-click → `Remove plugin`
4. Restart Calibre

## Getting Help

### Check the Logs

Calibre keeps detailed logs:
1. `Preferences → Miscellaneous → Get Calibre log`
2. Search for "kfxgen" to find relevant messages

### Report Issues

If you encounter problems:

1. Check this installation guide first
2. Check the README.md for known limitations
3. Check the conversion log for errors
4. Report issues at: https://github.com/[your-repo]/kfxgen/issues

Include:
- Calibre version
- Plugin version
- Error message from log
- Steps to reproduce

## Next Steps

After successful installation:

1. ✅ Convert a test book
2. ✅ Transfer to Kindle and verify it opens
3. ✅ Try with a Fast-Font (if you have one)
4. ✅ Convert your library!

## Version Information

**Current Version**: 0.1.0 (2025-01-28)

**Tested with**:
- Calibre 5.0+
- Kindle Paperwhite 3 and later
- macOS, Windows, Linux

**Known Limitations** (v0.1.0):
- 500 character text limit
- No images (uses template placeholder)
- Basic text formatting only
- Single chapter per book

See README.md for roadmap and planned features.

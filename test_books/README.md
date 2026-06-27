# kfxgen Test Books

This directory contains test EPUB files for the kfxgen Calibre plugin.

## fast-fonts-test.epub

**Purpose**: Test book designed for kfxgen v0.1.0 plugin

**Specifications**:
- **Character count**: 409 characters (under 500 limit)
- **Word count**: 62 words
- **Chapters**: 1
- **Format**: EPUB 3.0
- **Language**: English
- **Size**: ~2 KB

**Metadata**:
- **Title**: Fast-Fonts Test Book
- **Author**: kfxgen Contributors
- **Language**: en

**Content**:
```
Fast-Fonts Test Book

This is a test book for the kfxgen plugin. It demonstrates Fast-Fonts
support for Kindle devices.

When you install a Fast-Font on your Kindle and select it in the reading
settings, initial letters in each word will automatically appear bold.

This works because Fast-Fonts use OpenType contextual alternates, which
the Kindle renderer supports natively.
```

**Character count**: Well within the 500 character limit of kfxgen v0.1.0

## How to Use

### Test in Calibre

1. **Add to library**:
   ```
   Drag fast-fonts-test.epub into Calibre
   OR
   Right-click in library → Add books → Select fast-fonts-test.epub
   ```

2. **Convert to KFX**:
   ```
   Select the book
   Click "Convert books"
   Output format: KFX
   Click OK
   ```

3. **Check output**:
   ```
   Right-click book → Open containing folder
   Look for .kfx file
   Should be ~65-67 KB
   ```

### Test on Kindle

1. **Transfer to Kindle**:
   ```
   Connect Kindle via USB
   Copy the .kfx file to: documents/
   Safely eject
   ```

2. **Verify on Kindle**:
   ```
   Book should appear as "Fast-Fonts Test Book"
   Author: kfxgen Contributors
   Tap to open
   Content should display all text
   ```

3. **Test with Fast-Font** (optional):
   ```
   Install a Fast-Font in /mnt/us/fonts/ on Kindle
   Open the book
   Tap top → Aa → Fonts → Select your Fast-Font
   Initial letters should appear bold
   ```

## Expected Results

### Conversion Log

When converting, you should see:
```
kfxgen v0.1.0 - Starting conversion
Extracting metadata...
  Title: Fast-Fonts Test Book
  Author: kfxgen Contributors
  Language: en
Extracting text content...
  Content length: 409 characters
Generating KFX file...
✓ KFX file generated successfully
```

### Output File

- **Name**: `fast-fonts-test.kfx`
- **Size**: ~65-67 KB
- **Structure**: Valid KFX with proper headers
- **Opens on**: Kindle Paperwhite 3+ and newer

### On Kindle

- ✅ Book appears in library
- ✅ Opens without error
- ✅ All text visible (409 characters)
- ✅ Proper metadata (title, author)
- ✅ Works with Fast-Fonts (if installed)

## Troubleshooting

### Conversion Fails

**Error: "Content too long"**
- This shouldn't happen (409 < 500)
- Check plugin version (should be 0.1.0)

**Error: "Template file not found"**
- Plugin installation incomplete
- Reinstall kfxgen-plugin.zip

### Book Won't Open on Kindle

1. Check file size (~65KB)
2. Check extension (.kfx not .epub)
3. Try with POC 9 v5 file to verify Kindle compatibility

### Content Truncated

- In v0.1.0, content is limited to 500 chars
- This test book has 409 chars
- All content should appear

## Creating Your Own Test Books

To create EPUBs compatible with kfxgen v0.1.0:

1. **Keep text under 500 characters**
   - Extract text from HTML
   - Remove tags and whitespace
   - Count: should be < 500

2. **Use standard EPUB structure**:
   ```
   book.epub
   ├── mimetype
   ├── META-INF/
   │   └── container.xml
   └── OEBPS/
       ├── content.opf
       ├── toc.ncx
       └── chapter1.xhtml
   ```

3. **Include metadata**:
   - Title (required)
   - Author (required)
   - Language (optional, defaults to 'en')

4. **Package correctly**:
   ```bash
   zip -X0 book.epub mimetype
   zip -Xr9 book.epub META-INF OEBPS
   ```

## Version Compatibility

| kfxgen Version | Character Limit | Test Book |
|----------------|-----------------|-----------|
| 0.1.0 | 500 chars | ✅ fast-fonts-test.epub (409 chars) |
| 0.2.0 (planned) | Unlimited | TBD |

## Source Files

The unpackaged EPUB structure is in:
```
test_books/minimal_test_book/
├── mimetype
├── META-INF/
│   └── container.xml
└── OEBPS/
    ├── content.opf
    ├── toc.ncx
    └── chapter1.xhtml
```

You can modify these files and repackage to create custom test books.

## Additional Test Cases

Future test books to add:

- [ ] **edge-case-500.epub** - Exactly 500 characters
- [ ] **unicode-test.epub** - Unicode characters (Arabic, Chinese, etc.)
- [ ] **multi-chapter.epub** - Multiple chapters (tests concatenation)
- [ ] **long-text.epub** - >500 chars (tests truncation warning)
- [ ] **no-metadata.epub** - Missing title/author (tests defaults)

## License

Test books are provided under CC0 (public domain) for testing purposes.

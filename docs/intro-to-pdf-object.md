An Introduction to PDF Object Definitions in dpml
===

## 1. Understanding PDF Structure
A PDF file is fundamentally an indexed collection of objects, where each object represents a structured data unit. The file structure consists of four main components:

1. A header
2. Object definitions
3. A cross-reference table
4. A trailer

The cross-reference table serves as a lookup directory, mapping each numbered object to its byte offset location within the file. The trailer contains critical metadata, including the location of the root object (document catalog), which serves as the entry point for PDF interpretation. The file concludes with a byte offset pointing to the cross-reference table.

Here's an illustrative example of a PDF file structure:

```pdf
%PDF-2.0
1 0 obj
<<
  /Pages 2 0 R
  /Type /Catalog
>>
endobj
2 0 obj
<<
  /Count 1
  /Kids [
    3 0 R
  ]
  /Type /Pages
>>
endobj
3 0 obj
<<
  /Contents 4 0 R
  /MediaBox [ 0 0 612 792 ]
  /Parent 2 0 R
  /Resources <<
    /Font << /F1 5 0 R >>
  >>
  /Type /Page
>>
endobj
4 0 obj
<<
  /Length 44
>>
stream
BT
  /F1 24 Tf
  72 720 Td
  (Potato) Tj
ET
endstream
endobj
5 0 obj
<<
  /BaseFont /Helvetica
  /Encoding /WinAnsiEncoding
  /Subtype /Type1
  /Type /Font
>>
endobj

xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000062 00000 n 
0000000133 00000 n 
0000000277 00000 n 
0000000372 00000 n 
trailer <<
  /Root 1 0 R
  /Size 6
  /ID [<42841c13bbf709d79a200fa1691836f8><b1d8b5838eeafe16125317aa78e666aa>]
>>
startxref
478
%%EOF
```

### PDF File Interpretation
When a PDF viewer processes a file, it follows these steps:

1. Starts at the file's end to locate the cross-reference table offset
2. Accesses the cross-reference table to find object locations
3. Reads the trailer dictionary to identify the document catalog
4. Uses the document catalog to access various document components:
   - Pages
   - Outlines
   - Thumbnails
   - Annotations
   - Other PDF elements

The pages tree root is particularly crucial as it enables navigation to specific pages within the document.

### Example Interpretation Flow
Let's trace through our example:

1. The cross-reference table begins at byte offset 478 (indicated after `startxref`)
2. The trailer identifies object 1 as the document catalog (`/Root 1 0 R`)
3. Object 1 is located at byte offset 9
4. The document catalog points to object 2 as the pages tree root
5. Object 2 is found at byte offset 62
6. The pages tree identifies page 3 as the first page
7. Object 3 is positioned at byte offset 133
8. Object 3 defines the page properties and links to object 4 for content
9. Object 4, at byte offset 277, contains the drawing instructions for rendering "Potato"

This structure enables efficient random access to any part of the PDF document.

## 2. PDF Objects

Earlier, we discussed PDF objects and introduced the concept of dictionaries. At the top level of a PDF file, objects are identified by two numbers followed by the keyword "obj". The first number serves as the object number, while the second—known as the generation number—is typically 0. Everything between these identifiers and the "endobj" keyword constitutes the object's body.

The PDF specification provides a mechanism for modifying files by appending object updates and cross-reference table entries. When an object's contents are completely replaced (rather than modified), its generation number can be incremented. This allows object numbers to be reused while preventing old indirect references from resolving to new objects. However, such files are rare in practice, and generation numbers can generally be disregarded. Modern PDF specifications using object streams have even eliminated generation numbers entirely.

PDF objects share similarities with data structures found in JSON, YAML, and modern programming languages, though PDF includes some unique object types. Here are the available PDF object types:

- String: A text sequence enclosed in parentheses, e.g., (potato). Note that PDF strings typically don't support full Unicode encoding, though there are specific cases where this is possible. (A detailed discussion of character encoding is beyond our current scope.)

- Number: Both integers and floating-point numbers (e.g., 12, 3.14159). While the PDF specification distinguishes between integers and real numbers, they're often interchangeable in practice—integers can be used where real numbers are expected, and viewers typically handle real numbers appropriately when integers are required.

- Boolean: Simple true/false values

- Null: Represented by the keyword "null"

- Name: A keyword or dictionary key identifier starting with a forward slash (/), e.g., /Type

- Array: An ordered collection of objects enclosed in square brackets, with no separators between items. Arrays support nested structures, including other arrays and dictionaries. Example: `[1 (two) 3.14 false]`

- Dictionary: A collection of key-value pairs where keys are Names and values can be any object type. Dictionaries are enclosed in << and >> with no separators between entries. Example: `<< /A 1 /B [2, 3 <</Four 4>> ] >>`

- Indirect object reference: A reference to a numbered object in the file, consisting of two numbers (object and generation) followed by 'R', e.g., 1 0 R. While some objects must be direct per the PDF specification, most can be defined at the top level and referenced indirectly.

- Stream: A container for binary data, structured as a dictionary (containing at least a /Length key and other format-specific entries) followed by the specified number of bytes between "stream" and "endstream" keywords. 🔍 The stream length can be specified as an indirect object, enabling single-pass PDF generation where the stream length isn't known in advance—a common practice in PDF creation.

## 3. PDF Object Definitions In dpml

### Coordinate system definition

The positive x-axis extends horizontally to the right, while the positive y-axis extends vertically upward, following
standard mathematical conventions. The unit length along both the x and y axes is defined as 1/72 inch (or 1 point).

## 4. Useful Information

- [PDF32000_2008](https://opensource.adobe.com/dc-acrobat-sdk-docs/pdfstandards/PDF32000_2008.pdf) page 111: Table 51 - Operator Categories
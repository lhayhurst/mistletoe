"""
Built-in block-level token classes.
"""

from itertools import zip_longest
import mistletoe.block_tokenizer as tokenizer
import mistletoe.span_token as span_token


__all__ = ['Heading', 'Quote', 'BlockCode', 'Separator', 'List', 'Table',
           'FootnoteBlock']


def tokenize(lines, root=None):
    """
    A wrapper around block_tokenizer.tokenize. Pass in all block-level
    token constructors as arguments to block_tokenizer.tokenize.

    Doing so (instead of importing block_token module in block_tokenizer)
    avoids cyclic dependency issues, and allows for future injections of
    custom token classes.

    _token_types variable is at the bottom of this module.

    See also: block_tokenizer.tokenize, span_token.tokenize_inner.
    """
    return tokenizer.tokenize(lines, _token_types, Paragraph, root)


def add_token(token_cls):
    """
    Allows external manipulation of the parsing process.
    This function is called in BaseRenderer.__enter__.

    Arguments:
        token_cls (SpanToken): token to be included in the parsing process.
    """
    _token_types.insert(1, token_cls)


def remove_token(token_cls):
    """
    Allows external manipulation of the parsing process.
    This function is called in BaseRenderer.__exit__.

    Arguments:
        token_cls (SpanToken): token to be removed from the parsing process.
    """
    _token_types.remove(token_cls)


class BlockToken(object):
    """
    Base class for span-level tokens. Recursively parse inner tokens.

    Naming conventions:
        * lines denotes a list of (possibly unparsed) input lines, and is
          commonly used as the argument name for constructors.
        * self.children is a generator with all the inner tokens (thus if a
          token has children attribute, it is not a leaf node; if a token
          calls span_token.tokenize_inner, it is the boundary between
          span-level tokens and block-level tokens);
        * match is a static method used by tokenize to check if line_buffer
          matches the current token. Every subclass of BlockToken must
          define a match function (see block_tokenizer.tokenize).

    Attributes:
        children (generator object): inner tokens.
    """
    def __init__(self, lines, tokenize_func):
        self.children = tokenize_func(lines)


class Document(BlockToken):
    """
    Document token.
    """
    def __init__(self, lines):
        self.footnotes = {}
        # Document tokens have immediate access to first-level block tokens.
        # Useful for footnotes, etc.
        self.children = list(tokenize(lines, root=self))


class Heading(BlockToken):
    """
    Heading token. (["### some heading ###\n"])
    Boundary between span-level and block-level tokens.

    Attributes:
        level (int): heading level.
        children (generator): inner tokens.
    """
    def __init__(self, lines):
        if len(lines) == 1:    # ATX heading
            hashes, content = lines[0].split('# ', 1)
            content = content.split(' #', 1)[0].strip()
            self.level = len(hashes) + 1
        else:                  # setext heading
            if lines[-1][0] == '=':
                self.level = 1
            elif lines[-1][0] == '-':
                self.level = 2
            content = ' '.join([line.strip() for line in lines[:-1]])
        super().__init__(content, span_token.tokenize_inner)

    @staticmethod
    def match(lines):
        # ATX heading
        if (len(lines) == 1
                and lines[0].startswith('#')
                and lines[0].find('# ') != -1):
            return True
        # setext heading
        return len(lines) > 1 and (lines[-1].startswith('---')
                                   or lines[-1].startswith('==='))


class Quote(BlockToken):
    """
    Quote token. (["> # heading\n", "> paragraph\n"])
    """
    def __init__(self, lines):
        content = []
        for line in lines:
            if line.startswith('> '):
                content.append(line[2:])
            else:  # lazy continuation
                content.append(line)
        super().__init__(content, tokenize)

    @staticmethod
    def match(lines):
        return lines[0].startswith('> ')


class Paragraph(BlockToken):
    """
    Paragraph token. (["some\n", "continuous\n", "lines\n"])
    Boundary between span-level and block-level tokens.
    """
    def __init__(self, lines):
        content = ''.join(lines).replace('\n', ' ').strip()
        self.children = span_token.tokenize_inner(content)


class BlockCode(BlockToken):
    """
    Block code. (["```sh\n", "rm -rf /", ..., "```"])
    Boundary between span-level and block-level tokens.

    Attributes:
        children (iterator): contains a single span_token.RawText token.
        language (str): language of code block (default to empty).
    """
    def __init__(self, lines):
        if lines[0].startswith('```'):  # code fence
            content = ''.join(lines[1:-1])
            self.language = lines[0].strip()[3:]
        else:                           # indented code
            content = ''.join([line[4:] for line in lines])
            self.language = ''
        self.children = (span_token.RawText(content),)

    @staticmethod
    def match(lines):
        if lines[0].startswith('```') and lines[-1] == '```\n':
            return True
        for line in lines:
            if not line.startswith(' '*4):
                return False
        return True


class List(BlockToken):
    """
    List tokens. (["- item\n", "    - nested item\n", "- item\n" ])
    Boundary between span-level and block-level tokens.

    Attributes:
        children (list): inner tokens (ListItem or List).
        start (int): first index of ordered list (undefined if unordered).
    """
    def __init__(self, lines):
        # Every codebase needs a line of code that its author
        # doesn't understand. This is that line of code.
        # I need to cast this generator into a list because... why?
        # Someone please open a pull request and enlighten me...
        self.children = list(List.build_list(lines))
        leader = lines[0].split(' ', 1)[0]
        if leader[:-1].isdigit():
            self.start = int(leader[:-1])
        else:
            self.start = None

    @staticmethod
    def build_list(lines):
        """
        Constructor helper; builds a list from lines.

        The basic control structure looks something like this:

        - Does the current line have a valid leader?
            * yes: we are in a normal line
            * no:
                + Does it start with four spaces?
                    - yes:
                        * Does it have a valid leader?
                            + yes: we are in a nested list
                            - no:  we are in a lazy-continuation line
                    - no: we are in a lazy-continuation line

        Yields:
            a stream of ListItems or sub-Lists.
        """
        line_buffer = []
        nested = False

        def clear_buffer():
            """
            After each clear_buffer() call, nested is always False,
            and line_buffer is always empty.
            """
            nonlocal nested, line_buffer
            if not line_buffer:
                # start of the list; nested = False
                return
            yield List(line_buffer) if nested else ListItem(line_buffer)
            nested = False
            line_buffer.clear()

        for line in lines:
            if List.has_valid_leader(line):
                yield from clear_buffer()
            elif line.startswith(' '*4):
                line = line[4:]
                if List.has_valid_leader(line):
                    if not nested:
                        yield from clear_buffer()
                    nested = True
            line_buffer.append(line)
        yield from clear_buffer()

    @staticmethod
    def has_valid_leader(line):
        """
        Helper function; mainly because _build_list is gross enough.

        Note: returns False if line starts with spaces.
        """
        return (line.startswith(('+ ', '- ', '* '))         # unordered
                or (line.split(' ', 1)[0][:-1].isdigit()))  # ordered

    @staticmethod
    def match(lines):
        return List.has_valid_leader(lines[0].strip())


class ListItem(BlockToken):
    """
    List item token. (["- item 1\n", "continued\n"])
    Boundary between span-level and block-level tokens.

    Should only be called by List._build_list().
    """
    def __init__(self, lines):
        line = ' '.join([line.strip() for line in lines])
        try:
            content = line.split(' ', 1)[1].strip()
        except IndexError:
            content = ''
        super().__init__(content, span_token.tokenize_inner)


class Table(BlockToken):
    """
    Table token.

    Attributes:
        has_header (bool): whether table has header row.
        column_align (list): align options for each column (default to [None]).
        children (generator): inner tokens (TableRows).
    """
    def __init__(self, lines):
        self.has_header = lines[1].find('---') != -1
        if self.has_header:
            self.column_align = [self.parse_align(column)
                    for column in self.split_delimiter(lines.pop(1))]
        else:
            self.column_align = [None]
        self.children = (TableRow(line, self.column_align) for line in lines)

    @staticmethod
    def split_delimiter(delimiter):
        """
        Helper function; returns a list of align options.

        Args:
            delimiter (str): e.g.: "| :--- | :---: | ---: |\n"

        Returns:
            a list of align options (None, 0 or 1).
        """
        return (column.strip() for column in delimiter[1:-2].split('|'))

    @staticmethod
    def parse_align(column):
        """
        Helper function; returns align option from cell content.

        Returns:
            None if align = left;
            0    if align = center;
            1    if align = right.
        """
        if column[:4] == ':---' and column[-4:] == '---:':
            return 0
        if column[-4:] == '---:':
            return 1
        return None

    @staticmethod
    def match(lines):
        for line in lines:
            if line[0] != '|' or line[-2] != '|':
                return False
        return True


class TableRow(BlockToken):
    """
    Table row token.

    Should only be called by Table.__init__().
    """
    def __init__(self, line, row_align=None):
        self.row_align = [None] if row_align is None else row_align
        cells = line[1:-2].split('|')
        self.children = (TableCell(cell.strip(), align)
                         for cell, align in zip_longest(cells, self.row_align))


class TableCell(BlockToken):
    """
    Table cell token.
    Boundary between span-level and block-level tokens.

    Should only be called by TableRow.__init__().

    Attributes:
        align (bool): align option for current cell (default to None).
        children (generator): inner (span-)tokens.
    """
    def __init__(self, content, align=None):
        self.align = align
        super().__init__(content, span_token.tokenize_inner)


class FootnoteBlock(BlockToken):
    """
    Footnote tokens.
    A collection of footnote entries.

    Attributes:
        children (list): footnote entry tokens.
    """
    def __init__(self, lines):
        self.children = [FootnoteEntry(line) for line in lines]

    @staticmethod
    def match(lines):
        for line in lines:
            content = line.strip()
            if not (content.startswith('[') and content.find(']:') != -1):
                return False
        return True


class FootnoteEntry(BlockToken):
    """
    Footnote entry tokens.
    Special tokens that aren't really boundaries (but kind of are).

    Should only be called by FootnoteBlock.__init__().

    Attributes:
        key (str): key of footnote entry.
        value (str): value of footnote entry.
    """
    def __init__(self, line):
        key, value = line.strip().split(']:')
        self.key = key[1:]
        self.value = value.strip()


class Separator(BlockToken):
    """
    Separator token (a.k.a. horizontal rule.)
    """
    _acceptable_patterns = frozenset(('---\n', '* * *\n', '***\n', '===\n'))
    def __init__(self, lines):
        self.lines = lines

    @staticmethod
    def match(lines):
        return len(lines) == 1 and lines[0] in Separator._acceptable_patterns


"""
Tokens to be included in the parsing process, in the order specified.
"""
_token_types = [Heading, Quote, BlockCode, Separator, List,
                Table, FootnoteBlock]

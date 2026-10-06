def join_fields(fields: list[str], sep: str = ",", quote: str = '"') -> str:
    """Join ``fields`` into one delimited line, quoting the fields that need it.

    A field is wrapped in ``quote`` when it contains the separator, the quote
    character or a line break, and quote characters inside it are doubled, as
    in CSV. Other fields are written as they are.
    """
    needs_quoting = re.compile("[" + re.escape(sep + quote) + "\n]").search
    doubled = quote + quote
    return sep.join(
        [
            quote + value.replace(quote, doubled) + quote if needs_quoting(value) else value
            for value in fields
        ]
    )

def join_fields(fields: list[str], sep: str = ",", quote: str = '"') -> str:
    """Join ``fields`` into one delimited line, quoting the fields that need it.

    A field is wrapped in ``quote`` when it contains the separator, the quote
    character or a line break, and quote characters inside it are doubled, as
    in CSV. Other fields are written as they are.
    """
    doubled = quote + quote
    return sep.join(
        [
            f"{quote}{value.replace(quote, doubled)}{quote}"
            if sep in value or quote in value or "\n" in value
            else value
            for value in fields
        ]
    )

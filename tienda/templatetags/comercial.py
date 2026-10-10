from decimal import Decimal, InvalidOperation

from django import template


register = template.Library()


@register.filter
def cop(value):
    """Presenta un valor Decimal como moneda colombiana sin convertirlo a float."""
    if value in (None, ""):
        return "-"
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return "-"
    if amount == amount.to_integral_value():
        rendered = f"{amount:,.0f}"
    else:
        rendered = f"{amount:,.2f}"
    rendered = rendered.replace(",", "#").replace(".", ",").replace("#", ".")
    return f"${rendered}"

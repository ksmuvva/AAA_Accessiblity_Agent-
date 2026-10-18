"""What a label or URL says an action would do: the one destructive-intent check, in three widening tiers.

`destroys` (delete, cancel, sign out) is skipped even by the forms probe, whose writes the network guard blocks.
`irreversible` adds buying, paying and sending: refused for agents' clicks, links and opened URLs. `submits` adds plain
submit wording, which only blind probes skip: an agent may press Submit, because the guard blocks the write it sends.
"""

from __future__ import annotations

import re
import unicodedata


def _phrases(*words: str) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(words) + r")\b")


# Matched against normalise()d text, so hyphens, camelCase and accents don't matter (de, fr, es, nl included).
_DESTROYS = _phrases(
    r"delete", r"erase", r"destroy", r"deactivate", r"remove (?:account|all)", r"close (?:my |your )?account",
    r"revoke", r"cancel (?:my |your )?(?:account|subscription|order|membership)", r"unsubscribe", r"opt ?out",
    r"(?:account|subscription|membership|order) (?:cancel|close|delete)", r"(?:log|sign) ?(?:out|off)",
    r"(?:empty|clear) (?:the |your )?(?:basket|cart|bag|trash)", r"cart (?:empty|clear)",
    r"abmelden", r"ausloggen", r"loschen", r"kundigen", r"supprimer", r"deconnexion", r"se deconnecter", r"desabonner",
    r"eliminar", r"borrar", r"cerrar sesion", r"darse de baja", r"verwijderen", r"uitloggen", r"afmelden", r"opzeggen")
_SPENDS_OR_SENDS = _phrases(
    r"buy", r"purchase", r"pay(?: now)?", r"payment", r"place (?:an |your |the )?order", r"order now", r"book now",
    r"(?:complete|confirm) (?:order|purchase|booking|payment)", r"send", r"subscribe", r"publish", r"donate",
    r"transfer", r"withdraw", r"kaufen", r"bezahlen", r"bestellen", r"senden", r"abonnieren", r"acheter", r"payer",
    r"commander", r"envoyer", r"comprar", r"pagar", r"enviar", r"kopen", r"betalen", r"verzenden")
_SUBMITS = _phrases(r"submit", r"confirm", r"order", r"check ?out", r"remove", r"absenden", r"bestatigen", r"valider")


def normalise(text: str | None) -> str:
    """"Sign-Out", "/Account/LogOff", "Löschen" -> "sign out", "account log off", "loschen"."""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text or "")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[\W_]+", " ", text).lower()


def _first(text: str | None, *patterns: re.Pattern) -> str | None:
    norm = normalise(text)
    return next((m.group(0) for p in patterns if (m := p.search(norm))), None)


def destroys(text: str | None) -> str | None:
    """The phrase that says the action deletes, cancels or signs out (what a blocked write can't stop), or None."""
    return _first(text, _DESTROYS)


def irreversible(text: str | None) -> str | None:
    """destroys, plus buying, paying, sending or publishing: what agents, links and opened URLs must never do."""
    return _first(text, _DESTROYS, _SPENDS_OR_SENDS)


def submits(text: str | None) -> str | None:
    """irreversible, plus plain submit wording: what a blind probe must not press."""
    return _first(text, _DESTROYS, _SPENDS_OR_SENDS, _SUBMITS)


# Runs on an element: the text, value and names that say what it does.
DESCRIBE_JS = """(el) => [el.innerText, el.value, el.getAttribute('aria-label'), el.getAttribute('title'),
  el.getAttribute('href'), el.getAttribute('name'), el.id].filter(v => typeof v === 'string' && v.trim())
  .join(' | ').slice(0, 300)"""

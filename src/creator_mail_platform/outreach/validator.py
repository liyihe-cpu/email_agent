from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

import dns.exception
import dns.resolver
from sqlalchemy import or_, select

from ..core.db import session_scope
from ..core.models import ActivityContact
from ..core.utils import extract_send_address


VALIDATION_PAGE_SIZE = 5_000
BATCH_VALIDATION_PAGE_SIZE = 100
DNS_WORKERS = 24
DNS_TIMEOUT_SECONDS = 1.5
DNS_SERVER_TIMEOUT_SECONDS = 0.75
HARD_DNS_FAILURES = {"NXDOMAIN", "NULL_MX", "NO_MAIL_ROUTE"}
SYNTAX_PASSED = "syntax_passed"
MAIL_ROUTE_PASSED = "mail_route_passed"


@dataclass(frozen=True)
class ValidationResult:
    email_status: str
    status_reason: str | None
    send_address: str | None


def validate_pending_contacts(
    *,
    batch_no: int | None = None,
    limit: int | None = None,
    check_mx: bool = True,
    progress: Callable[[dict[str, int]], None] | None = None,
) -> dict[str, int]:
    """Validate unknown contacts in bounded pages and reuse each domain result."""
    if batch_no is not None and batch_no < 1:
        raise ValueError("batch_no must be positive")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")

    counts = {
        "checked_creators": 0,
        "unique_emails_checked": 0,
        "possibly_usable": 0,
        "invalid": 0,
        "unknown": 0,
    }
    seen_emails: set[str] = set()
    domain_statuses: dict[str, str] = {}
    last_creator_id: str | None = None

    while limit is None or counts["checked_creators"] < limit:
        page_limit = (
            BATCH_VALIDATION_PAGE_SIZE
            if batch_no is not None
            else VALIDATION_PAGE_SIZE
        )
        if limit is not None:
            page_limit = min(page_limit, limit - counts["checked_creators"])

        with session_scope() as session:
            needs_precheck = (
                or_(
                    ActivityContact.status_reason.is_(None),
                    ActivityContact.status_reason != MAIL_ROUTE_PASSED,
                )
                if check_mx
                else ActivityContact.status_reason.is_(None)
            )
            statement = select(
                ActivityContact.creator_id,
                ActivityContact.email,
            ).where(
                ActivityContact.email_status == "unknown",
                ActivityContact.send_status == "pending",
                needs_precheck,
            )
            if batch_no is not None:
                statement = statement.where(ActivityContact.batch_no == batch_no)
            if last_creator_id is not None:
                statement = statement.where(ActivityContact.creator_id > last_creator_id)
            contact_rows = list(
                session.execute(
                    statement.order_by(ActivityContact.creator_id).limit(page_limit)
                )
            )
        if not contact_rows:
            break

        emails = {str(row.email) for row in contact_rows}
        counts["unique_emails_checked"] += len(emails - seen_emails)
        seen_emails.update(emails)
        if check_mx:
            _cache_domain_statuses(emails, domain_statuses)

        results = {
            email: _validate_with_domain_cache(email, check_mx, domain_statuses)
            for email in emails
        }
        creator_ids = [str(row.creator_id) for row in contact_rows]
        page_checked = 0
        with session_scope() as session:
            contacts = {
                contact.creator_id: contact
                for contact in session.scalars(
                    select(ActivityContact).where(
                        ActivityContact.creator_id.in_(creator_ids)
                    )
                )
            }
            for row in contact_rows:
                contact = contacts.get(str(row.creator_id))
                if (
                    contact is None
                    or contact.email_status != "unknown"
                    or contact.send_status != "pending"
                ):
                    continue
                result = results[str(row.email)]
                contact.email_status = result.email_status
                contact.status_reason = result.status_reason
                counts["checked_creators"] += 1
                counts[result.email_status] += 1
                page_checked += 1
        last_creator_id = str(contact_rows[-1].creator_id)
        if progress is not None:
            progress(
                {
                    **counts,
                    "page_checked": page_checked,
                    "batch_no": batch_no or 0,
                }
            )

    return counts


def _validate_with_domain_cache(
    raw_email: str,
    check_mx: bool,
    domain_statuses: dict[str, str],
) -> ValidationResult:
    address = extract_send_address(raw_email)
    if address is None:
        return ValidationResult("invalid", "invalid_format", None)
    if not check_mx:
        return ValidationResult("unknown", SYNTAX_PASSED, address)
    domain = _ascii_domain(address)
    if domain is None:
        return ValidationResult("invalid", "invalid_domain", None)
    return _result_for_domain(address, domain_statuses.get(domain, "TEMPFAIL"))


def _cache_domain_statuses(emails: set[str], cache: dict[str, str]) -> None:
    domains = sorted(
        {
            domain
            for email in emails
            if (address := extract_send_address(email))
            if (domain := _ascii_domain(address))
            if domain not in cache
        }
    )
    if not domains:
        return
    with ThreadPoolExecutor(max_workers=min(DNS_WORKERS, len(domains))) as executor:
        statuses = executor.map(_confirmed_domain_status, domains)
        cache.update(zip(domains, statuses, strict=True))


def _ascii_domain(address: str) -> str | None:
    try:
        return address.rsplit("@", 1)[1].encode("idna").decode("ascii").casefold()
    except (IndexError, UnicodeError):
        return None


def _result_for_domain(address: str, domain_status: str) -> ValidationResult:
    if domain_status in {"MX_PRESENT", "A_FALLBACK"}:
        return ValidationResult("unknown", MAIL_ROUTE_PASSED, address)
    if domain_status == "NXDOMAIN":
        return ValidationResult("invalid", "invalid_domain", None)
    if domain_status in {"NULL_MX", "NO_MAIL_ROUTE"}:
        return ValidationResult("invalid", "no_mail_route", None)
    return ValidationResult("unknown", "dns_temporary_error", address)


def _confirmed_domain_status(domain: str) -> str:
    """Require the same hard DNS failure twice; uncertainty remains sendable."""
    first = _query_domain_once(domain)
    if first not in HARD_DNS_FAILURES:
        return first
    second = _query_domain_once(domain)
    return first if second == first else "TEMPFAIL"


def _query_domain_once(domain: str) -> str:
    """Check MX and the standards-compliant A/AAAA fallback once."""
    resolver = dns.resolver.Resolver(configure=True)
    # Windows may list an unreachable DNS first; leave time for the fallback server.
    resolver.timeout = DNS_SERVER_TIMEOUT_SECONDS
    resolver.lifetime = DNS_TIMEOUT_SECONDS
    try:
        answer = resolver.resolve(domain, "MX", raise_on_no_answer=False)
        if answer.rrset:
            if any(str(record.exchange) == "." for record in answer):
                return "NULL_MX"
            return "MX_PRESENT"
    except dns.resolver.NXDOMAIN:
        return "NXDOMAIN"
    except (dns.exception.Timeout, dns.resolver.NoNameservers):
        return "TEMPFAIL"
    except dns.resolver.NoAnswer:
        pass
    except dns.exception.DNSException:
        return "TEMPFAIL"

    found_address = False
    temporary_failure = False
    for record_type in ("A", "AAAA"):
        try:
            answer = resolver.resolve(domain, record_type, raise_on_no_answer=False)
            found_address = found_address or bool(answer.rrset)
        except dns.resolver.NXDOMAIN:
            return "NXDOMAIN"
        except (dns.exception.Timeout, dns.resolver.NoNameservers):
            temporary_failure = True
        except dns.resolver.NoAnswer:
            continue
        except dns.exception.DNSException:
            temporary_failure = True
    if found_address:
        return "A_FALLBACK"
    return "TEMPFAIL" if temporary_failure else "NO_MAIL_ROUTE"

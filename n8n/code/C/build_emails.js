// Build one personalised email per user in the delivery set.
//
// Input: one item per user, with their un-notified matches (best first) and the
// product details needed to explain each match. The recipient is ALWAYS a test
// alias; the user's real address appears only masked, in a test-mode banner.

const settings = $('Check settings').first().json;
const users = $input.all().map((i) => i.json).filter((u) => u.user_id);
const today = new Date().toLocaleDateString('en-IN', { day: 'numeric', month: 'long', year: 'numeric' });

const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const rupees = (n) => {
  const v = Number(n);
  if (!Number.isFinite(v)) return null;
  if (v >= 1e7) return `₹${+(v / 1e7).toFixed(2)} crore`;
  if (v >= 1e5) return `₹${+(v / 1e5).toFixed(2)} lakh`;
  return `₹${v.toLocaleString('en-IN')}`;
};
const pct = (n) => `${+Number(n).toFixed(2)}%`;
const mask = (email) => {
  const [name, domain] = String(email ?? '').split('@');
  return name && domain ? `${name[0]}***@${domain}` : 'unknown';
};
const EMPLOYMENT = { salaried: 'salaried', self_employed: 'self-employed', business: 'business owner' };
const VERIFY = {
  employer_category: 'employer type', city: 'city of residence', business_vintage: 'years in business',
  business_turnover: 'business turnover', professional_qualification: 'professional qualification',
  tax_returns: 'income tax returns', other: 'additional documents', text_rule_not_checked: 'lender-specific conditions',
};

function rateText(p) {
  if (p.rate_min == null) return 'Rate on request';
  return p.rate_max != null && p.rate_max !== p.rate_min
    ? `${pct(p.rate_min)} – ${pct(p.rate_max)} p.a.`
    : `From ${pct(p.rate_min)} p.a.`;
}

// Plain-language reasons this user qualifies, from the product's own thresholds.
function reasons(u, p) {
  const out = [];
  if (p.min_income != null) out.push(`Your monthly income of ${rupees(u.income)} meets the ${rupees(p.min_income)} minimum.`);
  if (p.min_score != null) out.push(`Your credit score of ${u.credit_score} meets the ${p.min_score} minimum.`);
  out.push(`Open to ${EMPLOYMENT[u.employment_status] ?? u.employment_status} applicants of your age (${u.age}).`);
  return out;
}

function productBlock(u, p, i) {
  const amount = p.amount_max != null ? ` · up to ${rupees(p.amount_max)}` : '';
  const verify = (p.verify ?? []).map((v) => VERIFY[v] ?? v);
  const verifyLine = verify.length
    ? `<p style="margin:8px 0 0;color:#8a5300;font-size:13px">To confirm: ${esc(p.note || `the lender will check your ${verify.join(', ')}.`)}</p>`
    : '';
  const link = p.url ? ` · <a href="${esc(p.url)}" style="color:#b45309">Details</a>` : '';
  return `
  <tr><td style="padding:16px 0;border-top:1px solid #e3e6ec">
    <p style="margin:0;font-size:12px;color:#6b7280">#${i + 1} · ${esc(p.provider ?? '')}</p>
    <p style="margin:2px 0 0;font-size:16px;font-weight:600;color:#17202e">${esc(p.product_name)}</p>
    <p style="margin:4px 0 0;font-size:14px;color:#17202e">${esc(rateText(p))}${esc(amount)}${link}</p>
    <ul style="margin:8px 0 0;padding-left:18px;font-size:13px;color:#374151">
      ${reasons(u, p).map((r) => `<li>${esc(r)}</li>`).join('')}
    </ul>${verifyLine}
  </td></tr>`;
}

return users.map((u, index) => {
  const products = u.products ?? [];
  const firstName = String(u.name ?? '').trim().split(/\s+/)[0] || 'there';
  const recipient = settings.recipients[index % settings.recipients.length];
  const n = products.length;
  const subject = `${firstName}, you may qualify for ${n} personal loan${n === 1 ? '' : 's'}`;

  const html = `<!doctype html><html><body style="margin:0;background:#f4f6f8;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;border:1px solid #e3e6ec;border-radius:10px">
  <tr><td style="padding:12px 24px;background:#fdf0e1;border-radius:10px 10px 0 0;font-size:12px;color:#8a5300">
    Test mode: generated for user ${esc(u.user_id)} (${esc(mask(u.email))}) and redirected to this test inbox.
  </td></tr>
  <tr><td style="padding:24px 24px 8px">
    <p style="margin:0;font-size:20px;font-weight:600;color:#17202e">Hi ${esc(firstName)},</p>
    <p style="margin:10px 0 0;font-size:15px;line-height:1.5;color:#374151">
      Based on your profile (${esc(EMPLOYMENT[u.employment_status] ?? u.employment_status)}, monthly income ${esc(rupees(u.income))}, credit score ${esc(u.credit_score)}),
      you appear eligible for these personal loans, best match first.
    </p>
  </td></tr>
  <tr><td style="padding:0 24px 8px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">
    ${products.map((p, i) => productBlock(u, p, i)).join('')}
  </table></td></tr>
  <tr><td style="padding:16px 24px 24px;border-top:1px solid #e3e6ec;font-size:12px;line-height:1.5;color:#6b7280">
    Indicative only, not an offer of credit. Rates and criteria come from lenders' public pages as of ${esc(today)};
    final eligibility, rate and amount are decided by the lender after verification.
  </td></tr>
</table></td></tr></table></body></html>`;

  return {
    json: {
      batch_id: settings.batch_id,
      user_id: u.user_id,
      original_email: mask(u.email),
      recipient,
      from: `${settings.sender_name} <${settings.sender_email}>`,
      subject,
      html,
      product_ids: products.map((p) => p.product_id),
    },
  };
});

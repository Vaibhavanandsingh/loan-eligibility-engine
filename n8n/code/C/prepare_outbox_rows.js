// Turn each email (and, in SES mode, its send result) into an outbox row.
//
// Reached from two branches: dry-run (input = the built emails) or SES (input =
// one SES response per email, paired back to its email). A match is marked
// notified only for status SENT, i.e. after SES accepted the message.

const settings = $('Check settings').first().json;
const emails = $('Build emails').all().map((i) => i.json);
const sqlText = (v) => (v == null ? 'NULL' : `'${String(v).replace(/'/g, "''")}'`);

return $input.all().map((item, index) => {
  const email = emails[item.pairedItem?.item ?? index];
  let status = 'DRY_RUN';
  let messageId = null;
  let error = null;

  if (settings.mode === 'ses') {
    const r = item.json ?? {};
    messageId = r.MessageId ?? r.messageId ?? r.SendEmailResult?.MessageId
      ?? r.SendEmailResponse?.SendEmailResult?.MessageId ?? null;
    if (r.error || !messageId) {
      status = 'FAILED';
      error = String(r.error?.message ?? r.error ?? 'SES returned no MessageId').slice(0, 500);
    } else {
      status = 'SENT';
    }
  }

  const productIds = email.product_ids.filter(Number.isInteger);
  return {
    json: {
      user_id: email.user_id,
      recipient: email.recipient,
      status,
      q_batch: sqlText(email.batch_id),
      q_user: sqlText(email.user_id),
      q_original: sqlText(email.original_email),
      q_recipient: sqlText(email.recipient),
      q_subject: sqlText(email.subject),
      q_html: sqlText(email.html),
      q_products: `ARRAY[${productIds.join(',')}]::int[]`,
      q_mode: sqlText(settings.mode),
      q_status: sqlText(status),
      q_message_id: sqlText(messageId),
      q_error: sqlText(error),
    },
  };
});

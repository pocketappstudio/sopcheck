# SOPCheck Privacy Policy

**Version 2026-10-12-r4 · Effective date: October 12, 2026**


This Privacy Policy explains how **{{OWNER_LEGAL_NAME}}** ("we", "us") collects, uses, and shares information when organizations and their users use SOPCheck (the "Service"). It forms part of our [Terms of Service](/terms).

## 1. Information we collect

**Account information:**

- user name and login identifier;
- a hashed password (we never store plaintext passwords);
- role (manager or technician);
- technician position and site;
- company name, time zone, and shift settings.

**Customer Content:**

- documents you upload, and the text extracted from them (including OCR);
- checklists generated from your documents, and your edits to them;
- safety-review results;
- checklist completions submitted by technicians, such as checkmarks, numeric readings, notes, photos, signatures, and co-signer names.

**Records of use and acceptance:**

- audit-log entries, such as uploads, edits, publishing with the reviewer attestation, and exports;
- login times and IP address at login;
- browser user-agent for sessions;
- the version, time, and IP address at which each user accepted these policies.

**Billing information:**

- your plan;
- the Gumroad license key you enter, which we keep so we can periodically re-verify your membership with Gumroad;
- verification results.

Payment card details are collected and processed by **Gumroad**, not by us.

**Usage metering:** counts of documents parsed and of technician seats, and AI token usage, which we track to enforce plan limits and control costs.

## 2. How we use information

We use information to do the following:

- provide and secure the Service;
- generate and review checklists;
- distribute checklists to the right technicians;
- enforce plan limits and verify memberships;
- keep audit trails for your organization;
- troubleshoot problems;
- comply with law.

We **do not sell** personal information. We **do not use Customer Content to train AI models**.

## 3. Subprocessors and sharing

We share information only with service providers that help us run the Service, and only as needed for that purpose.

| Subprocessor | Purpose | Data |
|---|---|---|
| **OpenAI** (API) | Turning document text into draft checklists; optional safety-sentence classification and text embeddings | Extracted document text and draft checklist content |
| Cloud hosting provider (third-party infrastructure provider in the United States) | Application hosting, database, and file storage | All Service data |
| **Gumroad** | Membership sales, billing, refunds, and license verification | Purchase and license data (Gumroad's own privacy policy applies) |

**About OpenAI.** As of the effective date, OpenAI's published policy says that data sent through its API is not used to train OpenAI models by default. OpenAI may retain API inputs and outputs for a limited period, for example up to 30 days for abuse monitoring, unless a different arrangement applies. We do not control OpenAI's practices, which may change. Please review OpenAI's current policies yourself.

We may also disclose information if the law requires it, to protect rights and safety, or as part of a merger or acquisition.

## 4. Retention


| Data | How long we keep it |
|---|---|
| Customer Content (uploaded SOP text, checklists and their versions, completions, photos, signatures) | While the workspace exists. When a manager deletes the workspace, it is locked right away and permanently deleted **7 days** later. |
| Workspaces on an expired trial or a lapsed plan | Kept read-only for up to **12 months** so you can export or reactivate them. After that we may delete them following a notice shown in the Service. |
| Audit log and policy-acceptance records (Terms acceptance, publish attestations, exports) | For the life of the workspace. They are deleted along with the workspace. |
| Deletion record ("tombstone") | Up to **3 years** after a workspace is deleted. It contains only internal IDs, a one-way hash of the workspace identifier, timestamps, and row counts. It holds no names, logins, or content. We keep it to show the deletion happened. |
| Sign-in session records | Sessions expire automatically. Expired session records are deleted **90 days** after they expire. |
| Deleted user accounts | Name, login, sign-in history, IP addresses and signatures are erased **immediately** on deletion. Completed checklist items stay in the employer's workspace records, attributed to "Deleted user". |
| Backups | Deleted data may stay in encrypted backups for up to **30 days** until the backups rotate out. |
| Billing records | Held by Gumroad under Gumroad's own policies. The Service stores only the plan and the last 4 characters of your license key. |

## 5. Deletion and your choices

Everything below is self-service in the in-app **Account** page.

- **Export.** Any user can download a JSON copy of their data. Managers' exports also include the entire company workspace: users, documents, checklist versions, completions, safety flags, the audit log, and a list of uploaded files. Passwords and license keys are never included.
- **Delete my account.** Technicians, and any manager who is not the last one, can delete their own account. Their login is disabled immediately and their personal data is erased or anonymized, as described in Section 4.
- **Delete the company workspace.** A manager confirms by typing the company name. The workspace locks immediately and can still be exported. Any manager can cancel during the **7-day** grace period. After that, all of the workspace's data, including uploaded files and photos, is permanently deleted, leaving only the minimal deletion record described above.

**Closing a paid account.** Cancel your membership in **Gumroad**, then delete the workspace in the Service. Deleting the workspace does not cancel a Gumroad subscription.

**Requests from individual users.** A technician's employer controls the workspace and its records. A technician can delete their own account at any time. The employer's manager can deactivate users or delete the workspace.

Depending on where you live, you may have rights to access, correct, delete, or port your personal information. The Account page provides access (export), portability (JSON), and deletion. Other requests can be sent through Gumroad's messaging feature, and we will respond as the applicable law requires.

## 6. Security

We use the following measures:

- encryption in transit (HTTPS);
- hashed passwords;
- httpOnly session cookies with server-side revocation;
- strict per-company data separation;
- role-based access;
- audit logging.

No system is perfectly secure. You are responsible for deciding which documents you upload (see Terms Section 3).

## 7. Children

The Service is for businesses and is not directed to children under 16.

## 8. International users

The Service is operated from the United States. Information may be processed in the United States and in other countries where our subprocessors operate.

## 9. Changes

We will update the version above when this Policy changes. For material changes, we will ask users to accept the new version in the Service.

## 10. Contact

Data export and deletion are self-service on the in-app **Account** page. Privacy questions, support, billing and refund matters, and legal notices go through Gumroad's messaging feature for this product (message the seller from your Gumroad purchase or library).

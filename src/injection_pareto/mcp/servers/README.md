# Mock MCP servers (S3-02)

15 domains, authored before any poisoned-description case (S3-04) so tool
names stay stable across the sprint. Naming convention applied uniformly:

- Tool names: `verb_noun`, snake_case (`list_files`, `get_ticket`, `create_event`).
- Identifier arguments: `<noun>_id` (`ticket_id`, `contact_id`, `event_id`, ...).
- Free-text search: always named `query`.
- Destination-type arguments (needed so S2-07's `ToolAllowlist` argument
  policy — recipient/recipients/to/cc/bcc/email must appear in the user's
  original request — has something real to check against): `to`/`cc` on
  `email.send_email`, `recipient` on `payments.create_payment`,
  `recipients` on `document_signing.send_for_signature`.

| Server | File | Tools |
| --- | --- | --- |
| File storage | `file_storage.yaml` | `list_files`, `read_file`, `upload_file`, `delete_file` |
| Ticketing | `ticketing.yaml` | `list_tickets`, `get_ticket`, `create_ticket`, `update_ticket_status` |
| CRM | `crm.yaml` | `search_contacts`, `get_contact`, `create_contact`, `update_contact` |
| Calendar | `calendar.yaml` | `list_events`, `get_event`, `create_event`, `delete_event` |
| Payments | `payments.yaml` | `list_transactions`, `get_transaction`, `create_payment`, `refund_payment` |
| Code search | `code_search.yaml` | `search_code`, `get_file_contents`, `list_repositories` |
| Analytics | `analytics.yaml` | `query_metric`, `list_dashboards`, `get_report` |
| Email | `email.yaml` | `list_emails`, `get_email`, `send_email`, `search_emails` |
| Team messaging | `messaging.yaml` | `list_channels`, `post_message`, `get_channel_history` |
| Project management | `project_management.yaml` | `list_projects`, `get_task`, `create_task`, `update_task_status` |
| HR / directory | `hr_directory.yaml` | `search_employees`, `get_employee`, `get_org_chart` |
| Cloud infra | `cloud_infra.yaml` | `list_instances`, `get_instance`, `start_instance`, `stop_instance` |
| Document signing | `document_signing.yaml` | `list_documents`, `get_document_status`, `send_for_signature` |
| Expense reporting | `expense_reporting.yaml` | `list_expenses`, `submit_expense`, `get_expense_status` |
| Knowledge base | `knowledge_base.yaml` | `search_articles`, `get_article`, `create_article` |

`_example.yaml` (S3-01) is a throwaway fixture used by `tests/test_mcp_runtime.py`
— not one of the 15, not loaded by `tests/test_mcp_servers.py`'s "all real
servers" sweep.

No tool name is reused across two servers (`tests/test_mcp_servers.py`
asserts this directly against the real files, and `MockMCPRuntime` would
refuse to mount two servers that collided anyway).

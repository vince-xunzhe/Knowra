-- User-controlled learning lifecycle for paper review.
-- Values are application-level enum strings:
--   not_started / learning / completed

alter table papers add column if not exists learning_status text;

update papers
set learning_status = 'not_started'
where coalesce(learning_status, '') not in ('not_started', 'learning', 'completed');

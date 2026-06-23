-- ============================================================================
-- 0008_rec_summary_marks.sql
--   1. recommendations.summary — a desktop-generated local-LLM summary pushed
--      up so mobile (which runs no local model) can show it. Global column
--      (single-user deployment); a per-user split can come later if needed.
--   2. rec_marks — per-user "saved / 收藏" marks, so a mark set on mobile
--      syncs to desktop (where it prompts the user to download the PDF).
-- ============================================================================

alter table recommendations add column if not exists summary text;

create table if not exists rec_marks (
  user_id     uuid not null references auth.users(id) on delete cascade,
  arxiv_id    text not null,
  created_at  timestamptz not null default now(),
  primary key (user_id, arxiv_id)
);

alter table rec_marks enable row level security;
drop policy if exists tenant_isolation on rec_marks;
create policy tenant_isolation on rec_marks
  for all
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

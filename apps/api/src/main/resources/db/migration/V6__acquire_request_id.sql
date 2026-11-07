-- One id per acquire request, chosen by the worker and kept across its own retries. A retry after a lost response
-- gets back the attempt it already created, instead of being refused as a duplicate and dropping the job.
ALTER TABLE job_attempts ADD COLUMN acquire_id UUID;

package com.motionmirror.jobs;

import java.util.Map;
import java.util.Set;

/** The job state machine. PostgreSQL decides; RabbitMQ only transports. */
public enum JobState {
    QUEUED, RUNNING, RETRY_WAIT, CANCEL_REQUESTED, SUCCEEDED, FAILED, CANCELLED;

    private static final Map<JobState, Set<JobState>> ALLOWED = Map.of(
            QUEUED, Set.of(RUNNING, CANCELLED),
            RUNNING, Set.of(SUCCEEDED, RETRY_WAIT, FAILED, CANCEL_REQUESTED),
            RETRY_WAIT, Set.of(QUEUED, CANCELLED),
            CANCEL_REQUESTED, Set.of(CANCELLED, SUCCEEDED, FAILED),
            // A user retry starts a fresh dispatch from a finished failure.
            FAILED, Set.of(QUEUED),
            CANCELLED, Set.of(QUEUED),
            SUCCEEDED, Set.of());

    public boolean canMoveTo(JobState next) {
        return ALLOWED.get(this).contains(next);
    }

    public boolean isTerminal() {
        return this == SUCCEEDED || this == FAILED || this == CANCELLED;
    }
}

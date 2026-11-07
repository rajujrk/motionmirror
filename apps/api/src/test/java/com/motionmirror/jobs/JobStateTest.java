package com.motionmirror.jobs;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

class JobStateTest {

    @Test
    void aSucceededJobNeverMovesAgain() {
        for (JobState next : JobState.values()) {
            assertThat(JobState.SUCCEEDED.canMoveTo(next)).isFalse();
        }
    }

    @Test
    void aQueuedJobCanOnlyStartOrBeCancelled() {
        assertThat(JobState.QUEUED.canMoveTo(JobState.RUNNING)).isTrue();
        assertThat(JobState.QUEUED.canMoveTo(JobState.CANCELLED)).isTrue();
        assertThat(JobState.QUEUED.canMoveTo(JobState.SUCCEEDED)).isFalse();
    }

    @Test
    void runningCancelGoesThroughCancelRequested() {
        assertThat(JobState.RUNNING.canMoveTo(JobState.CANCELLED)).isFalse();
        assertThat(JobState.RUNNING.canMoveTo(JobState.CANCEL_REQUESTED)).isTrue();
        assertThat(JobState.CANCEL_REQUESTED.canMoveTo(JobState.CANCELLED)).isTrue();
    }
}

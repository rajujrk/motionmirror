package com.motionmirror.jobs;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.motionmirror.assets.ObjectStorage;
import com.motionmirror.identity.CurrentUser;
import com.motionmirror.shared.ApiException;
import com.motionmirror.support.Fixtures;
import com.motionmirror.support.IntegrationTest;
import com.motionmirror.workers.LeaseService;
import com.motionmirror.workers.WorkerDtos;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.simple.JdbcClient;

@IntegrationTest
class LeaseIT {

    private static final Set<String> MOCK = Set.of("mock");

    @Autowired LeaseService leases;
    @Autowired Fixtures fixtures;
    @Autowired JobStore jobs;
    @Autowired JobService jobService;
    @Autowired JdbcClient jdbc;
    @Autowired ObjectStorage storage;

    UUID owner;
    UUID project;

    @BeforeEach
    void setUp() {
        owner = fixtures.user();
        project = fixtures.project(owner);
    }

    private JobRow job() {
        return fixtures.queuedJob(owner, project, JobKind.PREVIEW);
    }

    private LeaseService.AcquireResult acquire(JobRow job, String worker) {
        return leases.acquire(job.id(), UUID.randomUUID(), job.dispatchGeneration(), worker, MOCK);
    }

    private WorkerDtos.AcquireResponse acquired(JobRow job, String worker) {
        return ((LeaseService.Acquired) acquire(job, worker)).response();
    }

    private JobState state(UUID id) {
        return jobs.find(id).orElseThrow().state();
    }

    private String upload(WorkerDtos.AcquireResponse attempt, UUID jobId, String name, byte[] body) throws Exception {
        WorkerDtos.ArtifactAccessResponse access = leases.artifactAccess(jobId,
                new WorkerDtos.ArtifactAccessRequest(attempt.attemptId(), attempt.fencingToken(), List.of(), List.of(name)));
        WorkerDtos.ArtifactWrite write = access.writes().get(name);
        HttpResponse<String> response = HttpClient.newHttpClient().send(
                HttpRequest.newBuilder(URI.create(write.url()))
                        .header("Content-Type", write.contentType())
                        .PUT(HttpRequest.BodyPublishers.ofByteArray(body)).build(),
                HttpResponse.BodyHandlers.ofString());
        assertThat(response.statusCode()).isEqualTo(200);
        return write.key();
    }

    private WorkerDtos.CompleteRequest completion(WorkerDtos.AcquireResponse attempt, String key, int size) {
        return new WorkerDtos.CompleteRequest(attempt.attemptId(), attempt.fencingToken(),
                List.of(new WorkerDtos.OutputRef("result.mp4", key, "b".repeat(64), size, "video/mp4", Map.of())), Map.of(), null);
    }

    private void expireLease(UUID attemptId) {
        jdbc.sql("UPDATE job_attempts SET lease_expires_at = now() - interval '1 second' WHERE id = :id").param("id", attemptId).update();
    }

    @Test
    void concurrentDeliveriesGetExactlyOneWinner() throws Exception {
        JobRow job = job();
        try (ExecutorService pool = Executors.newFixedThreadPool(8)) {
            List<Callable<LeaseService.AcquireResult>> calls = java.util.stream.IntStream.range(0, 8)
                    .<Callable<LeaseService.AcquireResult>>mapToObj(index -> () -> acquire(job, "worker-" + index)).toList();
            List<Future<LeaseService.AcquireResult>> results = pool.invokeAll(calls);
            long winners = 0;
            for (Future<LeaseService.AcquireResult> result : results) {
                if (result.get() instanceof LeaseService.Acquired) {
                    winners++;
                }
            }
            assertThat(winners).isEqualTo(1);
        }
        assertThat(jobs.find(job.id()).orElseThrow().attemptCount()).isEqualTo(1);
    }

    @Test
    void aDuplicateDeliveryDoesNotExecuteWhileTheLeaseIsHeld() {
        JobRow job = job();
        acquired(job, "first");
        LeaseService.AcquireResult second = acquire(job, "second");
        assertThat(second).isEqualTo(new LeaseService.Refused(LeaseService.DUPLICATE, "an attempt is already running"));
    }

    @Test
    void aMessageFromAnOlderDispatchIsObsolete() {
        JobRow job = job();
        LeaseService.AcquireResult result = leases.acquire(job.id(), UUID.randomUUID(), job.dispatchGeneration() + 5, "w", MOCK);
        assertThat(((LeaseService.Refused) result).disposition()).isEqualTo(LeaseService.OBSOLETE);
        assertThat(state(job.id())).isEqualTo(JobState.QUEUED);
    }

    @Test
    void aWorkerWithoutTheBackendIsRefused() {
        JobRow job = job();
        assertThatThrownBy(() -> leases.acquire(job.id(), UUID.randomUUID(), 1, "w", Set.of("wan-animate")))
                .isInstanceOfSatisfying(ApiException.class, error -> assertThat(error.code()).isEqualTo("BACKEND_NOT_PERMITTED"));
    }

    @Test
    void aStaleWorkerCannotHeartbeatOrCompleteAfterExpiry() throws Exception {
        JobRow job = job();
        WorkerDtos.AcquireResponse first = acquired(job, "first");
        String key = upload(first, job.id(), "result.mp4", new byte[] {1, 2, 3});
        expireLease(first.attemptId());

        WorkerDtos.AcquireResponse second = acquired(job, "second");
        assertThat(second.fencingToken()).isGreaterThan(first.fencingToken());

        assertThatThrownBy(() -> leases.heartbeat(job.id(), first.attemptId(), first.fencingToken()))
                .isInstanceOfSatisfying(ApiException.class, error -> assertThat(error.code()).isEqualTo("LEASE_LOST"));
        assertThatThrownBy(() -> leases.complete(job.id(), completion(first, key, 3)))
                .isInstanceOfSatisfying(ApiException.class, error -> assertThat(error.code()).isEqualTo("LEASE_LOST"));
        assertThat(jobs.outputs(job.id())).isEmpty();
        assertThat(state(job.id())).isEqualTo(JobState.RUNNING);
    }

    @Test
    void completionIsIdempotentSoARepeatAfterALostAckConverges() throws Exception {
        JobRow job = job();
        WorkerDtos.AcquireResponse attempt = acquired(job, "w");
        String key = upload(attempt, job.id(), "result.mp4", new byte[] {1, 2, 3});

        assertThat(leases.complete(job.id(), completion(attempt, key, 3))).isEqualTo(JobState.SUCCEEDED);
        assertThat(leases.complete(job.id(), completion(attempt, key, 3))).isEqualTo(JobState.SUCCEEDED);

        assertThat(jobs.outputs(job.id())).hasSize(1);
        LeaseService.AcquireResult redelivered = acquire(job, "other");
        assertThat(((LeaseService.Refused) redelivered).disposition()).isEqualTo(LeaseService.DUPLICATE);
    }

    @Test
    void anUploadSucceedsWithTheContentTypeTheApiSaysItSigned() throws Exception {
        JobRow job = job();
        WorkerDtos.AcquireResponse attempt = acquired(job, "w");
        for (String name : List.of("normalized.png", "result.mp4", "manifest.json", "no-extension")) {
            upload(attempt, job.id(), name, new byte[] {1, 2, 3});
        }
    }

    @Test
    void outputOutsideTheAttemptPrefixIsRejected() throws Exception {
        JobRow job = job();
        WorkerDtos.AcquireResponse attempt = acquired(job, "w");
        assertThatThrownBy(() -> leases.complete(job.id(), completion(attempt, "assets/someone-else/original", 3)))
                .isInstanceOfSatisfying(ApiException.class, error -> assertThat(error.code()).isEqualTo("OUTPUT_KEY_REJECTED"));
    }

    @Test
    void anOutputThatWasNeverUploadedIsRejected() {
        JobRow job = job();
        WorkerDtos.AcquireResponse attempt = acquired(job, "w");
        String key = LeaseService.attemptPrefix(attempt.attemptId()) + "result.mp4";
        assertThatThrownBy(() -> leases.complete(job.id(), completion(attempt, key, 3)))
                .isInstanceOfSatisfying(ApiException.class, error -> assertThat(error.code()).isEqualTo("OUTPUT_MISSING"));
        assertThat(state(job.id())).isEqualTo(JobState.RUNNING);
    }

    @Test
    void anOutputOfTheWrongSizeIsRejected() throws Exception {
        JobRow job = job();
        WorkerDtos.AcquireResponse attempt = acquired(job, "w");
        String key = upload(attempt, job.id(), "result.mp4", new byte[] {1, 2, 3});
        assertThatThrownBy(() -> leases.complete(job.id(), completion(attempt, key, 999)))
                .isInstanceOfSatisfying(ApiException.class, error -> assertThat(error.code()).isEqualTo("OUTPUT_SIZE_MISMATCH"));
    }

    @Test
    void aRetryableFailureWaitsThenRunsOutOfAttempts() {
        JobRow job = job();
        for (int attemptNumber = 1; attemptNumber <= 3; attemptNumber++) {
            WorkerDtos.AcquireResponse attempt = acquired(jobs.find(job.id()).orElseThrow(), "w");
            JobState next = leases.fail(job.id(), new WorkerDtos.FailRequest(attempt.attemptId(), attempt.fencingToken(), "TRANSIENT", "network", true));
            if (attemptNumber < 3) {
                assertThat(next).isEqualTo(JobState.RETRY_WAIT);
                assertThat(jobs.find(job.id()).orElseThrow().nextAttemptAt()).isNotNull();
                jdbc.sql("UPDATE generation_jobs SET state = 'QUEUED', next_attempt_at = NULL WHERE id = :id").param("id", job.id()).update();
            } else {
                assertThat(next).isEqualTo(JobState.FAILED);
            }
        }
    }

    @Test
    void outOfMemoryIsNotRetriedOrSilentlyDowngraded() {
        JobRow job = job();
        WorkerDtos.AcquireResponse attempt = acquired(job, "w");
        assertThat(leases.fail(job.id(), new WorkerDtos.FailRequest(attempt.attemptId(), attempt.fencingToken(), "CUDA_OOM", "out of memory", true)))
                .isEqualTo(JobState.FAILED);
        assertThat(jobs.find(job.id()).orElseThrow().failureCode()).isEqualTo("CUDA_OOM");
    }

    @Test
    void cancellingARunningJobIsSeenByHeartbeatAndFencesLateCompletion() throws Exception {
        JobRow job = job();
        WorkerDtos.AcquireResponse attempt = acquired(job, "w");
        String key = upload(attempt, job.id(), "result.mp4", new byte[] {1, 2, 3});

        CurrentUser user = new CurrentUser(owner, Set.of("ROLE_USER"));
        assertThat(jobService.cancel(user, job.id()).state()).isEqualTo(JobState.CANCEL_REQUESTED);
        assertThat(leases.heartbeat(job.id(), attempt.attemptId(), attempt.fencingToken()).cancelRequested()).isTrue();

        assertThat(leases.complete(job.id(), completion(attempt, key, 3))).isEqualTo(JobState.CANCELLED);
        assertThat(jobs.outputs(job.id())).isEmpty();
    }

    @Test
    void cancellingAQueuedJobEndsItAndTheMessageBecomesHarmless() {
        JobRow job = job();
        CurrentUser user = new CurrentUser(owner, Set.of("ROLE_USER"));
        assertThat(jobService.cancel(user, job.id()).state()).isEqualTo(JobState.CANCELLED);
        assertThat(((LeaseService.Refused) acquire(job, "w")).disposition()).isEqualTo(LeaseService.CANCELLED);
    }

    @Test
    void retryCreatesANewGenerationSoTheOldMessageIsObsolete() {
        JobRow job = job();
        CurrentUser user = new CurrentUser(owner, Set.of("ROLE_USER"));
        jobService.cancel(user, job.id());
        JobRow retried = jobService.retry(user, job.id());

        assertThat(retried.state()).isEqualTo(JobState.QUEUED);
        assertThat(retried.dispatchGeneration()).isEqualTo(job.dispatchGeneration() + 1);
        assertThat(((LeaseService.Refused) leases.acquire(job.id(), UUID.randomUUID(), job.dispatchGeneration(), "w", MOCK)).disposition())
                .isEqualTo(LeaseService.OBSOLETE);
        assertThat(leases.acquire(job.id(), UUID.randomUUID(), retried.dispatchGeneration(), "w", MOCK)).isInstanceOf(LeaseService.Acquired.class);
    }

    @Test
    void workerUrlsNeedACurrentLease() {
        JobRow job = job();
        WorkerDtos.AcquireResponse attempt = acquired(job, "w");
        expireLease(attempt.attemptId());
        assertThatThrownBy(() -> leases.artifactAccess(job.id(),
                new WorkerDtos.ArtifactAccessRequest(attempt.attemptId(), attempt.fencingToken(), List.of("source"), List.of())))
                .isInstanceOfSatisfying(ApiException.class, error -> assertThat(error.code()).isEqualTo("LEASE_LOST"));
    }

    @Test
    void workersCanOnlyReadNamedInputs() {
        JobRow job = job();
        WorkerDtos.AcquireResponse attempt = acquired(job, "w");
        assertThatThrownBy(() -> leases.artifactAccess(job.id(),
                new WorkerDtos.ArtifactAccessRequest(attempt.attemptId(), attempt.fencingToken(), List.of("assets/other/original"), List.of())))
                .isInstanceOfSatisfying(ApiException.class, error -> assertThat(error.code()).isEqualTo("UNKNOWN_INPUT"));
    }

    private int maxAttempts(UUID job) {
        return jdbc.sql("SELECT max_attempts FROM generation_jobs WHERE id = :id").param("id", job).query(Integer.class).single();
    }

    private WorkerDtos.AcquireResponse leaseFor(JobRow job) {
        return ((LeaseService.Acquired) leases.acquire(job.id(), UUID.randomUUID(), job.dispatchGeneration(), "w", Set.of(job.backendId()))).response();
    }

    @Test
    void aStorageOutageDoesntUseUpTheJobsAttempts() {
        UUID owner = fixtures.user();
        JobRow job = fixtures.queuedJob(owner, fixtures.project(owner), JobKind.PREVIEW);
        int before = maxAttempts(job.id());
        WorkerDtos.AcquireResponse attempt = leaseFor(job);
        assertThat(leases.fail(job.id(), new WorkerDtos.FailRequest(attempt.attemptId(), attempt.fencingToken(), "STORAGE_ERROR", "down", true)))
                .isEqualTo(JobState.RETRY_WAIT);
        assertThat(maxAttempts(job.id())).as("the attempt it spent is given back").isEqualTo(before + 1);
    }

    @Test
    void aStorageOutageStillRetriesOnTheLastOrdinaryAttempt() {
        UUID owner = fixtures.user();
        JobRow job = fixtures.queuedJob(owner, fixtures.project(owner), JobKind.PREVIEW);
        WorkerDtos.AcquireResponse attempt = leaseFor(job);
        jdbc.sql("UPDATE generation_jobs SET attempt_count = max_attempts WHERE id = :id").param("id", job.id()).update();
        assertThat(leases.fail(job.id(), new WorkerDtos.FailRequest(attempt.attemptId(), attempt.fencingToken(), "STORAGE_ERROR", "down", true)))
                .isEqualTo(JobState.RETRY_WAIT);
    }

    @Test
    void anOrdinaryFailureStillUsesAnAttempt() {
        UUID owner = fixtures.user();
        JobRow job = fixtures.queuedJob(owner, fixtures.project(owner), JobKind.PREVIEW);
        int before = maxAttempts(job.id());
        WorkerDtos.AcquireResponse attempt = leaseFor(job);
        leases.fail(job.id(), new WorkerDtos.FailRequest(attempt.attemptId(), attempt.fencingToken(), "INTERNAL_ERROR", "bug", true));
        assertThat(maxAttempts(job.id())).isEqualTo(before);
    }

    @Test
    void aLastingOutageEventuallyEndsTheJob() {
        UUID owner = fixtures.user();
        JobRow job = fixtures.queuedJob(owner, fixtures.project(owner), JobKind.PREVIEW);
        WorkerDtos.AcquireResponse attempt = leaseFor(job);
        for (int number = 100; number < 106; number++) {
            jdbc.sql("""
                    INSERT INTO job_attempts (id, job_id, attempt_no, worker_id, fencing_token, dispatch_generation, event_id, state, lease_expires_at, failure_code)
                    VALUES (:id, :job, :number, 'w', :number, 1, :event, 'FAILED', now(), 'STORAGE_ERROR')
                    """).param("id", UUID.randomUUID()).param("job", job.id()).param("number", number).param("event", UUID.randomUUID()).update();
        }
        jdbc.sql("UPDATE generation_jobs SET attempt_count = max_attempts WHERE id = :id").param("id", job.id()).update();
        assertThat(leases.fail(job.id(), new WorkerDtos.FailRequest(attempt.attemptId(), attempt.fencingToken(), "STORAGE_ERROR", "down", true)))
                .isEqualTo(JobState.FAILED);
    }
}

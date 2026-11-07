package com.motionmirror.workers;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.PositiveOrZero;
import jakarta.validation.constraints.Size;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/** Internal worker contract. Field names are snake_case to match the Python worker and the task envelope. */
public final class WorkerDtos {

    private WorkerDtos() {}

    public record RegisterRequest(
            @JsonProperty("worker_id") @NotBlank @Size(max = 128) String workerId,
            @NotNull List<String> backends,
            @JsonProperty("device_type") @Pattern(regexp = "cuda|mps|cpu") String deviceType,
            Map<String, Object> capabilities,
            List<String> adapters,
            Map<String, Object> runtime) {}

    public record AcquireRequest(
            @JsonProperty("event_id") @NotNull UUID eventId,
            @JsonProperty("dispatch_generation") @Positive int dispatchGeneration,
            @JsonProperty("worker_id") @NotBlank @Size(max = 128) String workerId) {}

    public record AcquireResponse(
            @JsonProperty("attempt_id") UUID attemptId,
            @JsonProperty("attempt_no") int attemptNo,
            @JsonProperty("fencing_token") long fencingToken,
            @JsonProperty("lease_expires_at") Instant leaseExpiresAt,
            Map<String, Object> request) {}

    public record Disposition(String disposition, String detail) {}

    public record Fence(
            @JsonProperty("attempt_id") @NotNull UUID attemptId,
            @JsonProperty("fencing_token") @PositiveOrZero long fencingToken) {}

    public record HeartbeatRequest(
            @JsonProperty("attempt_id") @NotNull UUID attemptId,
            @JsonProperty("fencing_token") @PositiveOrZero long fencingToken) {}

    public record HeartbeatResponse(
            @JsonProperty("lease_expires_at") Instant leaseExpiresAt,
            @JsonProperty("cancel_requested") boolean cancelRequested) {}

    public record ProgressRequest(
            @JsonProperty("attempt_id") @NotNull UUID attemptId,
            @JsonProperty("fencing_token") @PositiveOrZero long fencingToken,
            @NotBlank @Pattern(regexp = "validate|decode|segment|extract_motion|prepare_reference|generate|composite|encode|upload|verify") String stage,
            @Positive Integer current,
            @Positive Integer total,
            @Size(max = 64) String unit) {}

    public record OutputRef(
            @NotBlank @Pattern(regexp = "^[a-z0-9_.-]{1,64}$") String name,
            @NotBlank String key,
            @NotBlank @Pattern(regexp = "^[a-f0-9]{64}$") String sha256,
            @JsonProperty("size_bytes") @Positive long sizeBytes,
            @JsonProperty("content_type") @NotBlank String contentType,
            Map<String, Object> media) {}

    public record CompleteRequest(
            @JsonProperty("attempt_id") @NotNull UUID attemptId,
            @JsonProperty("fencing_token") @PositiveOrZero long fencingToken,
            @NotNull List<@Valid OutputRef> outputs,
            Map<String, Object> result,
            @JsonProperty("effective_settings") Map<String, Object> effectiveSettings) {}

    public record FailRequest(
            @JsonProperty("attempt_id") @NotNull UUID attemptId,
            @JsonProperty("fencing_token") @PositiveOrZero long fencingToken,
            @NotBlank @Pattern(regexp = "^[A-Z0-9_]{1,64}$") String code,
            @Size(max = 2000) String detail,
            boolean retryable) {}

    public record JobStatus(String state) {}

    public record ArtifactAccessRequest(
            @JsonProperty("attempt_id") @NotNull UUID attemptId,
            @JsonProperty("fencing_token") @PositiveOrZero long fencingToken,
            List<@Pattern(regexp = "^[a-z0-9_.:-]{1,64}$") String> reads,
            List<@Pattern(regexp = "^[a-z0-9_.-]{1,64}$") String> writes) {}

    /** The URL is signed for exactly this content type, so the worker must send the same one. */
    public record ArtifactWrite(String key, String url, @JsonProperty("content_type") String contentType) {}

    public record ArtifactAccessResponse(Map<String, String> reads, Map<String, ArtifactWrite> writes) {}
}

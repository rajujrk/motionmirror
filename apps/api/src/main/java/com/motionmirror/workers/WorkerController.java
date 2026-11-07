package com.motionmirror.workers;

import com.motionmirror.config.AppProperties;
import com.motionmirror.jobs.JobState;
import com.motionmirror.shared.ApiException;
import com.motionmirror.shared.Json;
import jakarta.validation.Valid;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** Worker permissions come from the verified token's client, never from what the worker says about itself. */
@RestController
@RequestMapping("/internal/v1")
public class WorkerController {

    private final LeaseService leases;
    private final JdbcClient jdbc;
    private final AppProperties properties;

    public WorkerController(LeaseService leases, JdbcClient jdbc, AppProperties properties) {
        this.leases = leases;
        this.jdbc = jdbc;
        this.properties = properties;
    }

    @PostMapping("/workers/register")
    public ResponseEntity<Void> register(@AuthenticationPrincipal Jwt token, @Valid @RequestBody WorkerDtos.RegisterRequest request) {
        Set<String> allowed = allowedBackends(token);
        if (!allowed.containsAll(request.backends())) {
            throw new ApiException(HttpStatus.FORBIDDEN, "BACKEND_NOT_PERMITTED", "This worker can't register those backends");
        }
        jdbc.sql("""
                INSERT INTO workers (worker_id, client_id, backends, device_type, capabilities, adapters, runtime)
                VALUES (:id, :client, CAST(:backends AS jsonb), :device, CAST(:capabilities AS jsonb), CAST(:adapters AS jsonb), CAST(:runtime AS jsonb))
                ON CONFLICT (worker_id) DO UPDATE SET client_id = EXCLUDED.client_id, backends = EXCLUDED.backends,
                    device_type = EXCLUDED.device_type, capabilities = EXCLUDED.capabilities, adapters = EXCLUDED.adapters,
                    runtime = EXCLUDED.runtime, last_seen = now()
                """)
                .param("id", request.workerId()).param("client", clientId(token))
                .param("backends", Json.write(request.backends())).param("device", request.deviceType())
                .param("capabilities", Json.write(request.capabilities() == null ? java.util.Map.of() : request.capabilities()))
                .param("adapters", Json.write(request.adapters() == null ? List.of() : request.adapters()))
                .param("runtime", Json.write(request.runtime() == null ? java.util.Map.of() : request.runtime()))
                .update();
        return ResponseEntity.noContent().build();
    }

    @PostMapping("/jobs/{id}/acquire")
    public ResponseEntity<?> acquire(@AuthenticationPrincipal Jwt token, @PathVariable UUID id,
            @Valid @RequestBody WorkerDtos.AcquireRequest request) {
        LeaseService.AcquireResult result = leases.acquire(id, request.eventId(), request.dispatchGeneration(),
                request.workerId(), allowedBackends(token));
        return switch (result) {
            case LeaseService.Acquired acquired -> ResponseEntity.ok(acquired.response());
            case LeaseService.Refused refused -> ResponseEntity.status(HttpStatus.CONFLICT)
                    .body(new WorkerDtos.Disposition(refused.disposition(), refused.detail()));
        };
    }

    @PostMapping("/jobs/{id}/heartbeat")
    public WorkerDtos.HeartbeatResponse heartbeat(@PathVariable UUID id, @Valid @RequestBody WorkerDtos.HeartbeatRequest request) {
        return leases.heartbeat(id, request.attemptId(), request.fencingToken());
    }

    @PostMapping("/jobs/{id}/progress")
    public ResponseEntity<Void> progress(@PathVariable UUID id, @Valid @RequestBody WorkerDtos.ProgressRequest request) {
        leases.progress(id, request);
        return ResponseEntity.noContent().build();
    }

    @PostMapping("/jobs/{id}/complete")
    public WorkerDtos.JobStatus complete(@PathVariable UUID id, @Valid @RequestBody WorkerDtos.CompleteRequest request) {
        JobState state = leases.complete(id, request);
        return new WorkerDtos.JobStatus(state.name());
    }

    @PostMapping("/jobs/{id}/fail")
    public WorkerDtos.JobStatus fail(@PathVariable UUID id, @Valid @RequestBody WorkerDtos.FailRequest request) {
        JobState state = leases.fail(id, request);
        return new WorkerDtos.JobStatus(state.name());
    }

    @PostMapping("/jobs/{id}/artifact-access")
    public WorkerDtos.ArtifactAccessResponse artifactAccess(@PathVariable UUID id, @Valid @RequestBody WorkerDtos.ArtifactAccessRequest request) {
        return leases.artifactAccess(id, request);
    }

    @PostMapping("/adapters/{id}/artifact-access")
    public WorkerDtos.AdapterAccessResponse adapterAccess(@PathVariable UUID id, @Valid @RequestBody WorkerDtos.AdapterAccessRequest request) {
        return leases.adapterAccess(id, request);
    }

    private Set<String> allowedBackends(Jwt token) {
        List<String> backends = properties.oidc().workerBackends().get(clientId(token));
        if (backends == null) {
            throw new ApiException(HttpStatus.FORBIDDEN, "UNKNOWN_WORKER_CLIENT", "This client is not a known worker");
        }
        return Set.copyOf(backends);
    }

    private static String clientId(Jwt token) {
        String client = token.getClaimAsString("azp");
        return client != null ? client : token.getClaimAsString("client_id");
    }
}

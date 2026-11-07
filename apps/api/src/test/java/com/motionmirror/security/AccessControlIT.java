package com.motionmirror.security;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.csrf;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.jwt;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.oidcLogin;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.motionmirror.jobs.JobKind;
import com.motionmirror.jobs.JobRow;
import com.motionmirror.shared.Json;
import com.motionmirror.support.Fixtures;
import com.motionmirror.support.IntegrationTest;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.OidcLoginRequestPostProcessor;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;

@IntegrationTest
class AccessControlIT {

    @Autowired MockMvc mvc;
    @Autowired Fixtures fixtures;
    @Autowired JdbcClient jdbc;

    private static OidcLoginRequestPostProcessor login(String subject) {
        return oidcLogin().idToken(token -> token.subject(subject).claim("iss", "https://issuer.test"));
    }

    private String createProject(String subject) throws Exception {
        String body = mvc.perform(post("/api/v1/projects").with(login(subject)).with(csrf())
                        .contentType(MediaType.APPLICATION_JSON).content("{\"name\":\"Mine\"}"))
                .andExpect(status().isCreated()).andReturn().getResponse().getContentAsString();
        return Json.read(body).path("id").asString();
    }

    private UUID userIdOf(String subject) {
        return jdbc.sql("SELECT id FROM users WHERE issuer = 'https://issuer.test' AND subject = :subject").param("subject", subject).query(UUID.class).single();
    }

    @Test
    void anonymousApiRequestsGet401NotARedirect() throws Exception {
        mvc.perform(get("/api/v1/projects")).andExpect(status().isUnauthorized());
    }

    @Test
    void mutationsNeedACsrfToken() throws Exception {
        mvc.perform(post("/api/v1/projects").with(login("csrf-user")).contentType(MediaType.APPLICATION_JSON).content("{\"name\":\"x\"}"))
                .andExpect(status().isForbidden());
    }

    @Test
    void anotherUsersProjectLooksLikeItDoesNotExist() throws Exception {
        String aliceProject = createProject("alice-" + UUID.randomUUID());
        mvc.perform(get("/api/v1/projects/" + aliceProject).with(login("mallory")))
                .andExpect(status().isNotFound()).andExpect(jsonPath("$.code").value("NOT_FOUND"));
        mvc.perform(post("/api/v1/projects/" + aliceProject + "/assets/upload-intents").with(login("mallory")).with(csrf())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(Json.write(Map.of("role", "DRIVING_VIDEO", "filename", "a.mp4", "sizeBytes", 5, "contentType", "video/mp4", "consentConfirmed", true))))
                .andExpect(status().isNotFound());
    }

    @Test
    void aGuessedRandomIdIsNotFound() throws Exception {
        mvc.perform(get("/api/v1/projects/" + UUID.randomUUID()).with(login("mallory"))).andExpect(status().isNotFound());
        mvc.perform(get("/api/v1/assets/" + UUID.randomUUID()).with(login("mallory"))).andExpect(status().isNotFound());
        mvc.perform(get("/api/v1/jobs/" + UUID.randomUUID()).with(login("mallory"))).andExpect(status().isNotFound());
    }

    @Test
    void anotherUsersJobAndDownloadsAreInvisible() throws Exception {
        UUID owner = fixtures.user();
        UUID project = fixtures.project(owner);
        JobRow job = fixtures.queuedJob(owner, project, JobKind.PREVIEW);
        UUID asset = fixtures.readyAsset(owner, project, "DRIVING_VIDEO");

        mvc.perform(get("/api/v1/jobs/" + job.id()).with(login("mallory"))).andExpect(status().isNotFound());
        mvc.perform(post("/api/v1/jobs/" + job.id() + "/cancel").with(login("mallory")).with(csrf())).andExpect(status().isNotFound());
        mvc.perform(post("/api/v1/jobs/" + job.id() + "/retry").with(login("mallory")).with(csrf())).andExpect(status().isNotFound());
        mvc.perform(get("/api/v1/jobs/" + job.id() + "/outputs/result.mp4/download-url").with(login("mallory"))).andExpect(status().isNotFound());
        mvc.perform(get("/api/v1/assets/" + asset + "/download-url").with(login("mallory"))).andExpect(status().isNotFound());
    }

    @Test
    void anAssetFromAnotherProjectCantBeReusedInASelection() throws Exception {
        String subject = "reuse-" + UUID.randomUUID();
        String projectA = createProject(subject);
        String projectB = createProject(subject);
        UUID owner = userIdOf(subject);
        UUID assetInB = fixtures.readyAsset(owner, UUID.fromString(projectB), "DRIVING_VIDEO");

        mvc.perform(post("/api/v1/projects/" + projectA + "/subject-selections").with(login(subject)).with(csrf())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(Json.write(Map.of("sourceAssetId", assetInB, "frameIndex", 0, "timestampNum", 0, "timestampDen", 30,
                                "coordinateMapping", Map.of(), "annotations", Map.of("points", List.of(Map.of("x", 1, "y", 2)))))))
                .andExpect(status().isNotFound());
    }

    @Test
    void anotherUsersAssetCantBeUsedAsAReference() throws Exception {
        String subject = "refs-" + UUID.randomUUID();
        String project = createProject(subject);
        UUID other = fixtures.user();
        UUID otherAsset = fixtures.readyAsset(other, fixtures.project(other), "TARGET_REFERENCE");

        mvc.perform(post("/api/v1/projects/" + project + "/appearance-profiles").with(login(subject)).with(csrf())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(Json.write(Map.of("name", "Me", "references", List.of(Map.of("assetId", otherAsset, "viewLabel", "FULL_BODY", "primary", true))))))
                .andExpect(status().isNotFound());
    }

    @Test
    void aFaceCloseUpCantBeThePrimaryReference() throws Exception {
        String subject = "face-" + UUID.randomUUID();
        String project = createProject(subject);
        UUID asset = fixtures.readyAsset(userIdOf(subject), UUID.fromString(project), "TARGET_REFERENCE");
        mvc.perform(post("/api/v1/projects/" + project + "/appearance-profiles").with(login(subject)).with(csrf())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(Json.write(Map.of("name", "Me", "references", List.of(Map.of("assetId", asset, "viewLabel", "FACE", "primary", true))))))
                .andExpect(status().isBadRequest()).andExpect(jsonPath("$.code").value("PRIMARY_MUST_SHOW_BODY"));
    }

    @Test
    void aBrowserSessionCantCallWorkerEndpoints() throws Exception {
        mvc.perform(post("/internal/v1/jobs/" + UUID.randomUUID() + "/heartbeat").with(login("alice"))
                        .contentType(MediaType.APPLICATION_JSON).content("{}"))
                .andExpect(status().isForbidden());
    }

    @Test
    void aTokenWithoutTheWorkerScopeIsForbidden() throws Exception {
        mvc.perform(post("/internal/v1/jobs/" + UUID.randomUUID() + "/heartbeat")
                        .with(jwt().authorities(new SimpleGrantedAuthority("SCOPE_profile")))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(Json.write(Map.of("attempt_id", UUID.randomUUID(), "fencing_token", 1))))
                .andExpect(status().isForbidden());
    }

    @Test
    void anUnknownWorkerClientCantAcquire() throws Exception {
        mvc.perform(post("/internal/v1/jobs/" + UUID.randomUUID() + "/acquire")
                        .with(jwt().jwt(token -> token.claim("azp", "someone-else")).authorities(new SimpleGrantedAuthority("SCOPE_worker")))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(Json.write(Map.of("event_id", UUID.randomUUID(), "dispatch_generation", 1, "worker_id", "w"))))
                .andExpect(status().isForbidden()).andExpect(jsonPath("$.code").value("UNKNOWN_WORKER_CLIENT"));
    }

    @Test
    void aWorkerCantRegisterABackendItsClientDoesntAllow() throws Exception {
        mvc.perform(post("/internal/v1/workers/register")
                        .with(jwt().jwt(token -> token.claim("azp", "motionmirror-worker-mock")).authorities(new SimpleGrantedAuthority("SCOPE_worker")))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(Json.write(Map.of("worker_id", "w", "backends", List.of("wan-animate"), "device_type", "cuda"))))
                .andExpect(status().isForbidden());
    }

    @Test
    void adminEndpointsNeedTheAdminRole() throws Exception {
        mvc.perform(get("/api/v1/admin/adapters").with(login("not-admin"))).andExpect(status().isForbidden());
    }

    @Test
    void responsesCarrySecurityHeadersAndARequestId() throws Exception {
        ResultActions result = mvc.perform(get("/api/v1/auth/me").with(login("headers-user"))).andExpect(status().isOk());
        result.andExpect(header().string("X-Frame-Options", "DENY"))
                .andExpect(header().string("X-Content-Type-Options", "nosniff"))
                .andExpect(header().exists("X-Request-Id"));
        assertThat(result.andReturn().getResponse().getHeader("Cache-Control")).contains("no-store");
    }

    @Test
    void aForgedRequestIdIsReplaced() throws Exception {
        String forged = "bad\nid";
        String returned = mvc.perform(get("/actuator/health/liveness").header("X-Request-Id", forged))
                .andReturn().getResponse().getHeader("X-Request-Id");
        assertThat(returned).doesNotContain("\n").isNotEqualTo(forged);
    }

    @Test
    void metricsAreNotPublic() throws Exception {
        mvc.perform(get("/actuator/prometheus")).andExpect(status().is4xxClientError());
    }
}

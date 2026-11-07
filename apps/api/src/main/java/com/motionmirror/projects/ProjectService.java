package com.motionmirror.projects;

import com.motionmirror.audit.AuditService;
import com.motionmirror.identity.CurrentUser;
import com.motionmirror.shared.ApiException;
import java.util.Map;
import java.util.UUID;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class ProjectService {

    private final ProjectRepository projects;
    private final AuditService audit;

    public ProjectService(ProjectRepository projects, AuditService audit) {
        this.projects = projects;
        this.audit = audit;
    }

    @Transactional
    public ProjectEntity create(CurrentUser user, String name) {
        ProjectEntity project = projects.save(new ProjectEntity(UUID.randomUUID(), user.id(), name));
        audit.user(user, "PROJECT_CREATED", "project", project.getId().toString(), Map.of());
        return project;
    }

    /** The ownership check for everything inside a project. Other users get the same answer as a missing id. */
    @Transactional(readOnly = true)
    public ProjectEntity requireOwned(CurrentUser user, UUID projectId) {
        return projects.findByIdAndOwnerIdAndStatus(projectId, user.id(), "ACTIVE")
                .orElseThrow(() -> ApiException.notFound("Project"));
    }

    @Transactional(readOnly = true)
    public Page<ProjectEntity> list(CurrentUser user, int page, int size) {
        return projects.findByOwnerIdAndStatusOrderByCreatedAtDesc(
                user.id(), "ACTIVE", PageRequest.of(Math.max(page, 0), Math.clamp(size, 1, 100)));
    }
}

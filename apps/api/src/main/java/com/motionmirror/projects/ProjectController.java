package com.motionmirror.projects;

import com.motionmirror.identity.CurrentUserService;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.springframework.data.domain.Page;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/projects")
public class ProjectController {

    public record CreateProject(@NotBlank @Size(max = 200) String name) {}

    public record ProjectView(UUID id, String name, String status, Instant createdAt, long version) {
        static ProjectView of(ProjectEntity project) {
            return new ProjectView(project.getId(), project.getName(), project.getStatus(),
                    project.getCreatedAt(), project.getVersion());
        }
    }

    public record ProjectPage(List<ProjectView> items, int page, int size, long total) {}

    private final ProjectService projects;
    private final CurrentUserService currentUser;

    public ProjectController(ProjectService projects, CurrentUserService currentUser) {
        this.projects = projects;
        this.currentUser = currentUser;
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public ProjectView create(@Valid @RequestBody CreateProject request) {
        return ProjectView.of(projects.create(currentUser.require(), request.name().strip()));
    }

    @GetMapping
    public ProjectPage list(@RequestParam(defaultValue = "0") int page, @RequestParam(defaultValue = "20") int size) {
        Page<ProjectEntity> result = projects.list(currentUser.require(), page, size);
        return new ProjectPage(result.map(ProjectView::of).getContent(), result.getNumber(), result.getSize(),
                result.getTotalElements());
    }

    @GetMapping("/{id}")
    public ProjectView get(@PathVariable UUID id) {
        return ProjectView.of(projects.requireOwned(currentUser.require(), id));
    }
}

package com.motionmirror.retention;

import com.motionmirror.identity.CurrentUserService;
import java.util.UUID;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1")
public class DeletionController {

    private final DeletionService deletion;
    private final CurrentUserService currentUser;

    public DeletionController(DeletionService deletion, CurrentUserService currentUser) {
        this.deletion = deletion;
        this.currentUser = currentUser;
    }

    @DeleteMapping("/projects/{id}")
    public ResponseEntity<Void> deleteProject(@PathVariable UUID id) {
        deletion.deleteProject(currentUser.require(), id);
        return ResponseEntity.noContent().build();
    }

    @DeleteMapping("/assets/{id}")
    public ResponseEntity<Void> deleteAsset(@PathVariable UUID id) {
        deletion.deleteAsset(currentUser.require(), id);
        return ResponseEntity.noContent().build();
    }

    @DeleteMapping("/jobs/{id}")
    public ResponseEntity<Void> deleteResult(@PathVariable UUID id) {
        deletion.deleteResult(currentUser.require(), id);
        return ResponseEntity.noContent().build();
    }
}

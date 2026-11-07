package com.motionmirror.projects;

import java.util.Optional;
import java.util.UUID;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ProjectRepository extends JpaRepository<ProjectEntity, UUID> {

    Optional<ProjectEntity> findByIdAndOwnerIdAndStatus(UUID id, UUID ownerId, String status);

    Page<ProjectEntity> findByOwnerIdAndStatusOrderByCreatedAtDesc(UUID ownerId, String status, Pageable pageable);
}

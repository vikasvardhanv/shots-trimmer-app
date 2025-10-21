// Terminal-style YouTube Shorts Trimmer JavaScript
class TerminalInterface {
    constructor() {
        // Only initialize if we're on a page with the conversion form
        this.form = document.getElementById('conversion-form');
        if (!this.form) {
            return; // Exit early if this is not the conversion page
        }
        
        this.progressSection = document.getElementById('progress-section');
        this.resultsSection = document.getElementById('results-section');
        this.errorSection = document.getElementById('error-section');
        this.progressBar = document.getElementById('progress-bar');
        this.progressText = document.getElementById('progress-text');
        this.reactionUrlGroup = document.getElementById('reaction-url-group');
        this.addReactionCheckbox = document.getElementById('add_reaction');
        
        this.currentJobId = null;
        this.isProcessing = false;
        
        this.initializeInterface();
        this.bindEvents();
    }

    initializeInterface() {
        console.log('🖥️  Terminal interface initialized');
        this.typeWriter('youtube-shorts-trimmer v2.0.0 ready for input...', document.querySelector('.terminal-title'));
        
        // Initialize terminal effects
        this.addTerminalEffects();
        
        // Set initial state
        this.hideAllSections();
    }

    bindEvents() {
        // Form submission
        if (this.form) {
            this.form.addEventListener('submit', (e) => this.handleFormSubmit(e));
        }
        
        // Reaction checkbox toggle
        if (this.addReactionCheckbox) {
            this.addReactionCheckbox.addEventListener('change', (e) => this.toggleReactionInput(e));
        }
        
        // Terminal window controls
        document.querySelectorAll('.terminal-control').forEach(control => {
            control.addEventListener('click', (e) => this.handleTerminalControl(e));
        });
        
        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => this.handleKeyboardShortcuts(e));
    }

    addTerminalEffects() {
        // Add blinking cursor effect to active input
        const urlInput = document.getElementById('url');
        if (urlInput) {
            urlInput.addEventListener('focus', () => {
                urlInput.classList.add('terminal-cursor');
            });
            urlInput.addEventListener('blur', () => {
                urlInput.classList.remove('terminal-cursor');
            });
        }
        
        // Add terminal sound effects (optional)
        this.playTerminalStartup();
    }

    playTerminalStartup() {
        // Create a subtle beep sound effect
        try {
            const audioContext = new (window.AudioContext || window.webkitAudioContext)();
            const oscillator = audioContext.createOscillator();
            const gainNode = audioContext.createGain();
            
            oscillator.connect(gainNode);
            gainNode.connect(audioContext.destination);
            
            oscillator.frequency.setValueAtTime(800, audioContext.currentTime);
            gainNode.gain.setValueAtTime(0.1, audioContext.currentTime);
            gainNode.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + 0.1);
            
            oscillator.start(audioContext.currentTime);
            oscillator.stop(audioContext.currentTime + 0.1);
        } catch (e) {
            console.log('Audio context not available');
        }
    }

    typeWriter(text, element, speed = 50) {
        if (!element) return;
        
        element.textContent = '';
        let i = 0;
        const timer = setInterval(() => {
            if (i < text.length) {
                element.textContent += text.charAt(i);
                i++;
            } else {
                clearInterval(timer);
            }
        }, speed);
    }

    toggleReactionInput(e) {
        const isChecked = e.target.checked;
        if (isChecked) {
            this.reactionUrlGroup.style.display = 'block';
            this.reactionUrlGroup.classList.add('fade-in');
            this.logTerminalMessage('REACTION_OVERLAY: enabled');
        } else {
            this.reactionUrlGroup.style.display = 'none';
            this.reactionUrlGroup.classList.remove('fade-in');
            this.logTerminalMessage('REACTION_OVERLAY: disabled');
        }
    }

    handleTerminalControl(e) {
        const control = e.target;
        if (control.classList.contains('control-close')) {
            this.logTerminalMessage('SYSTEM: close requested');
        } else if (control.classList.contains('control-minimize')) {
            this.logTerminalMessage('SYSTEM: minimize requested');
        } else if (control.classList.contains('control-maximize')) {
            this.logTerminalMessage('SYSTEM: maximize requested');
        }
    }

    handleKeyboardShortcuts(e) {
        // Ctrl+Enter to submit form
        if (e.ctrlKey && e.key === 'Enter' && !this.isProcessing) {
            e.preventDefault();
            this.form.dispatchEvent(new Event('submit'));
        }
        
        // Esc to cancel operation
        if (e.key === 'Escape' && this.isProcessing) {
            this.logTerminalMessage('USER: operation cancelled');
        }
    }

    async handleFormSubmit(e) {
        e.preventDefault();
        
        if (this.isProcessing) {
            this.logTerminalMessage('ERROR: operation already in progress');
            return;
        }

        this.isProcessing = true;
        this.hideAllSections();
        
        // Get form data
        const formData = {
            url: document.getElementById('url').value.trim(),
            num_shorts: parseInt(document.getElementById('num_shorts').value),
            clip_duration: parseInt(document.getElementById('clip_duration').value),
            add_reaction: document.getElementById('add_reaction').checked,
            reaction_url: document.getElementById('reaction_url').value.trim()
        };

        // Validate form
        if (!this.validateForm(formData)) {
            this.isProcessing = false;
            return;
        }

        // Log conversion start
        this.logTerminalMessage(`CONVERSION_START: processing ${formData.url}`);
        this.logTerminalMessage(`PARAMETERS: shorts=${formData.num_shorts}, duration=${formData.clip_duration}s`);
        
        // Start conversion
        await this.startConversion(formData);
    }

    validateForm(formData) {
        if (!formData.url) {
            this.showError('ERROR: YouTube URL is required');
            return false;
        }

        const youtubeRegex = /^(https?:\/\/)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)\/(watch\?v=|embed\/|v\/|.+\?v=)?([^&=%\?]{11})/;
        if (!youtubeRegex.test(formData.url)) {
            this.showError('ERROR: Invalid YouTube URL format');
            return false;
        }

        if (formData.add_reaction && !formData.reaction_url) {
            this.showError('ERROR: Reaction URL required when reaction overlay is enabled');
            return false;
        }

        return true;
    }

    async startConversion(formData) {
        this.showProgress();
        this.updateProgress(0, 'Initializing conversion process...', 'initializing');

        try {
            const response = await fetch('/convert', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(formData)
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.error || `HTTP ${response.status}: ${response.statusText}`);
            }

            const data = await response.json();
            
            if (data.error) {
                throw new Error(data.error);
            }

            this.currentJobId = data.progress_key;
            this.logTerminalMessage(`JOB_CREATED: ${this.currentJobId}`);
            
            // Start polling for progress
            this.pollProgress(this.currentJobId);

        } catch (error) {
            console.error('Conversion error:', error);
            this.logTerminalMessage(`CONVERSION_ERROR: ${error.message}`);
            this.showError(error.message || 'An error occurred during conversion');
            this.isProcessing = false;
        }
    }

    async pollProgress(progressKey, attempt = 0, maxAttempts = 300) {
        try {
            // Add timeout for very long processes (15 minutes max)
            if (attempt > 180) { // 180 * 3s = 9 minutes average
                this.logTerminalMessage('PROCESS_TIMEOUT: Processing is taking too long');
                this.showError('Processing is taking longer than expected. Please try with a shorter video or fewer clips.');
                return;
            }
            
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 15000);
            
            const response = await fetch(`/progress/${progressKey}`, {
                signal: controller.signal,
                headers: {
                    'Cache-Control': 'no-cache',
                    'Pragma': 'no-cache'
                }
            });
            
            clearTimeout(timeoutId);
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            const data = await response.json();
            this.logTerminalMessage(`POLL_${attempt + 1}: progress=${data.progress}% status=${data.status || 'processing'}`);

            // Handle the new backend response format
            if (data.status === 'error') {
                this.handleConversionError(data);
                return;
            }
            
            if (data.status === 'completed') {
                this.updateProgress(100, `Conversion completed! Created ${data.num_shorts || 0} shorts`, 'completed');
                // Set the current job ID for download links
                if (data.progress_key) {
                    this.currentJobId = data.progress_key;
                }
                this.handleConversionComplete(data);
                return;
            }
            
            // Handle numeric progress for processing
            if (typeof data.progress === 'number') {
                const progress = Math.max(0, Math.min(100, data.progress));
                
                let stage = this.getProcessingStage(progress);
                let message = this.getStageMessage(progress);
                
                this.updateProgress(progress, message, stage);
                
                // Check if completed
                if (progress >= 100) {
                    this.updateProgress(100, 'Processing complete, preparing download...', 'completed');
                    // Wait a bit then check for completion
                    setTimeout(() => this.pollProgress(progressKey, attempt + 1, maxAttempts), 1000);
                } else {
                    // Continue polling with faster interval for active processing
                    const pollInterval = progress < 20 ? 3000 : (progress > 80 ? 1000 : 1500);
                    setTimeout(() => this.pollProgress(progressKey, attempt + 1, maxAttempts), pollInterval);
                }
            } else {
                // Fallback: continue polling
                setTimeout(() => this.pollProgress(progressKey, attempt + 1, maxAttempts), 2000);
            }

        } catch (error) {
            this.handlePollingError(error, progressKey, attempt, maxAttempts);
        }
    }

    handleConversionComplete(progressData) {
        this.updateProgress(100, 'Conversion completed successfully!', 'completed');
        this.logTerminalMessage(`CONVERSION_COMPLETE: ${progressData.shorts?.length || 0} shorts generated`);
        this.logTerminalMessage(`OUTPUT_TITLE: ${progressData.title || 'Unknown'}`);
        
        document.title = 'Conversion Complete - YouTube Shorts Trimmer';
        
        setTimeout(() => {
            this.showResults(progressData);
            this.isProcessing = false;
        }, 1000);
    }

    handleConversionError(progressData) {
        const errorMessage = progressData.message || 'Conversion failed';
        this.logTerminalMessage(`CONVERSION_FAILED: ${errorMessage}`);
        this.updateProgress(0, 'Conversion failed - Processing stopped', 'error');
        
        document.title = 'Conversion Failed - YouTube Shorts Trimmer';
        
        // Immediately stop processing and show error
        this.isProcessing = false;
        this.showError(errorMessage);
    }

    handlePollingError(error, progressKey, attempt, maxAttempts) {
        this.logTerminalMessage(`POLL_ERROR_${attempt + 1}: ${error.message}`);
        
        if (attempt < maxAttempts) {
            const retryDelay = Math.min(10000, 2000 * Math.pow(1.5, Math.min(attempt, 8)));
            this.logTerminalMessage(`RETRY_IN: ${Math.round(retryDelay/1000)}s`);
            
            this.updateProgress(null, `Connection issue, retrying in ${Math.round(retryDelay/1000)}s...`, 'error');
            
            setTimeout(() => {
                this.pollProgress(progressKey, attempt + 1, maxAttempts);
            }, retryDelay);
        } else {
            this.logTerminalMessage('MAX_RETRIES_REACHED: giving up');
            this.showError('Connection failed after multiple attempts. Please refresh and try again.');
            this.isProcessing = false;
        }
    }

    getProcessingStage(progress) {
        if (progress < 5) return 'initializing';
        if (progress < 20) return 'downloading';
        if (progress < 85) return 'creating';
        if (progress < 95) return 'finalizing';
        return 'completed';
    }

    getStageMessage(progress) {
        if (progress < 5) return 'Initializing conversion process';
        if (progress < 20) return 'Downloading video from YouTube';
        if (progress < 85) return 'Creating and processing short clips';
        if (progress < 95) return 'Adding final touches and optimizations';
        return 'Conversion completed successfully';
    }

    hideAllSections() {
        if (this.progressSection) this.progressSection.style.display = 'none';
        if (this.resultsSection) this.resultsSection.style.display = 'none';
        if (this.errorSection) this.errorSection.style.display = 'none';
    }

    showProgress() {
        this.hideAllSections();
        if (this.progressSection) {
            this.progressSection.style.display = 'block';
            this.progressSection.classList.add('fade-in');
        }
        if (this.progressBar) {
            this.progressBar.style.width = '0%';
        }
    }

    updateProgress(percentage, message, stage = '') {
        this.logTerminalMessage(`PROGRESS_UPDATE: ${percentage}% - ${message}`);
        
        if (typeof percentage === 'number' && !isNaN(percentage)) {
            const clampedPercentage = Math.max(0, Math.min(100, percentage));
            const roundedPercentage = Math.round(clampedPercentage * 10) / 10;
            
            if (this.progressBar) {
                this.progressBar.style.transition = 'width 0.5s ease-in-out';
                this.progressBar.style.width = roundedPercentage + '%';
                this.progressBar.setAttribute('aria-valuenow', roundedPercentage);
            }
            
            const stageEmoji = this.getStageEmoji(stage);
            
            if (this.progressText) {
                this.progressText.innerHTML = `
                    <div class="d-flex justify-content-between align-items-center">
                        <span>${stageEmoji} ${message}</span>
                        <span class="badge bg-success">${roundedPercentage}%</span>
                    </div>
                    <div class="mt-2">
                        <small class="text-muted">
                            <i class="fas fa-terminal"></i> Stage: ${stage.toUpperCase()}
                        </small>
                    </div>
                `;
            }
            
            document.title = `${roundedPercentage}% - YouTube Shorts Trimmer`;
            
        } else {
            if (this.progressText) {
                this.progressText.innerHTML = `
                    <div class="d-flex align-items-center">
                        <span class="loading-spinner me-2"></span>
                        <span>${message || 'Processing...'}</span>
                    </div>
                `;
            }
        }
    }

    getStageEmoji(stage) {
        const stageMap = {
            'initializing': '🚀',
            'downloading': '⬇️',
            'creating': '✂️',
            'finalizing': '✨',
            'completed': '✅',
            'error': '❌'
        };
        return stageMap[stage] || '⚙️';
    }

    showResults(data) {
        this.hideAllSections();
        
        const downloadLinks = document.getElementById('download-links');
        if (downloadLinks) {
            downloadLinks.innerHTML = '';

            if (data.shorts && data.shorts.length > 0) {
                data.shorts.forEach((short, index) => {
                    const card = this.createDownloadCard(index + 1, data.title, this.currentJobId, index);
                    downloadLinks.appendChild(card);
                });

                if (this.resultsSection) {
                    this.resultsSection.style.display = 'block';
                    this.resultsSection.classList.add('fade-in');
                }
                
                this.logTerminalMessage(`DOWNLOAD_READY: ${data.shorts.length} files available`);
            } else {
                this.showError('No shorts were generated. Please try again.');
            }
        }
    }

    createDownloadCard(shortNumber, title, progressKey, index) {
        const col = document.createElement('div');
        col.className = 'col-md-6 col-lg-4 mb-3';

        col.innerHTML = `
            <div class="card download-card h-100">
                <div class="card-body text-center">
                    <div class="mb-3">
                        <i class="fas fa-file-video fa-3x text-success"></i>
                    </div>
                    <h6 class="card-title">short_${shortNumber}.mp4</h6>
                    <p class="card-text small text-muted">${title || 'Generated Short'}</p>
                    <a href="/download/${progressKey}/${index}" 
                       class="btn btn-download btn-sm" 
                       download>
                        <i class="fas fa-download"></i> DOWNLOAD
                    </a>
                </div>
            </div>
        `;

        return col;
    }

    showError(message) {
        this.hideAllSections();
        
        const errorElement = document.getElementById('error-message');
        if (errorElement) {
            errorElement.innerHTML = `
                <div class="alert alert-danger d-flex align-items-center" role="alert">
                    <i class="fas fa-exclamation-triangle me-3"></i>
                    <div>
                        <strong>Processing Failed:</strong><br>
                        ${message}
                    </div>
                </div>
                <div class="mt-3 text-center">
                    <button type="button" class="btn btn-primary" onclick="window.location.reload()">
                        <i class="fas fa-redo me-2"></i>Try Again
                    </button>
                    <a href="/api-access" class="btn btn-outline-secondary ms-2">
                        <i class="fas fa-book me-2"></i>API Documentation
                    </a>
                </div>
                <div class="mt-3">
                    <small class="text-muted">
                        <i class="fas fa-lightbulb me-1"></i>
                        <strong>Tips:</strong> Try a shorter video, reduce the number of clips, or check your internet connection.
                    </small>
                </div>
            `;
        }
        
        if (this.errorSection) {
            this.errorSection.style.display = 'block';
            this.errorSection.classList.add('fade-in');
        }
        this.logTerminalMessage(`ERROR_DISPLAY: ${message}`);
        
        // Stop any ongoing processing
        this.isProcessing = false;
        this.currentJobId = null;
    }

    logTerminalMessage(message) {
        if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
            const timestamp = new Date().toISOString().substring(11, 23);
            console.log(`[${timestamp}] ${message}`);
        }
    }
}

// Initialize terminal interface when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    window.terminal = new TerminalInterface();
    
    // Add terminal-style welcome messages in development
    if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
        setTimeout(() => {
            console.log('🖥️  YouTube Shorts Trimmer Terminal v2.0.0');
            console.log('📹 Ready for video processing operations');
            console.log('🔧 Type Ctrl+Enter to submit, Esc to cancel');
            console.log('📚 Visit /api-access for API documentation');
        }, 500);
    }
});
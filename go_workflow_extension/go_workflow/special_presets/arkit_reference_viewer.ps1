param(
    [Parameter(Mandatory = $true)]
    [string]$StateFile
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$InformationPreference = 'SilentlyContinue'
$VerbosePreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'

$PidFile = [System.IO.Path]::ChangeExtension($StateFile, '.pid')

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$script:ViewerSignature = ""
$script:ViewerMediaIndex = 0
$script:ViewerMediaCount = 0
$script:ViewerActivated = $false
$script:StateLastWriteUtc = [DateTime]::MinValue
$script:StateLastLength = -1
$script:MediaCache = @{}
$script:CurrentMediaKey = ""
$script:PreloadQueue = @()

function To-DisplayText {
    param([object]$Value)
    if ($null -eq $Value) {
        return ""
    }
    return [string]$Value
}

function Write-ViewerLog {
    param([string]$Message)
    try {
        $logPath = [System.IO.Path]::ChangeExtension($StateFile, '.log')
        $line = ("{0} {1}" -f ([DateTime]::Now.ToString("yyyy-MM-dd HH:mm:ss.fff")), $Message)
        [System.IO.File]::AppendAllText($logPath, $line + [Environment]::NewLine, [System.Text.Encoding]::UTF8)
    } catch {}
}

function Test-RemoteUrl {
    param([string]$Value)
    $text = To-DisplayText $Value
    return $text.StartsWith('http://') -or $text.StartsWith('https://')
}

function Get-BulletText {
    param([object[]]$Lines)
    if (-not $Lines) {
        return ""
    }
    $items = @()
    foreach ($line in $Lines) {
        $text = To-DisplayText $line
        if ($text.Trim()) {
            $items += ("- " + $text.Trim())
        }
    }
    return ($items -join [Environment]::NewLine)
}

function Clear-MediaCache {
    param([string[]]$KeepKeys = @())
    $keep = @{}
    foreach ($key in @($KeepKeys)) {
        if ($key) {
            $keep[$key] = $true
        }
    }
    foreach ($key in @($script:MediaCache.Keys)) {
        if ($keep.ContainsKey($key)) {
            continue
        }
        try {
            $entry = $script:MediaCache[$key]
            if ($null -ne $entry.Image) {
                $entry.Image.Dispose()
            }
            if ($null -ne $entry.Stream) {
                $entry.Stream.Dispose()
            }
        } catch {}
        $script:MediaCache.Remove($key)
    }
}

function Stop-CurrentMedia {
    param()
    Clear-MediaCache
}

function Get-MediaBytes {
    param([string]$Path)
    if (Test-RemoteUrl $Path) {
        $client = New-Object System.Net.WebClient
        try {
            $client.Headers.Set("User-Agent", "Mozilla/5.0 GoWorkflowReferenceViewer")
            return $client.DownloadData($Path)
        } finally {
            $client.Dispose()
        }
    }
    return [System.IO.File]::ReadAllBytes($Path)
}

function Get-MediaImage {
    param([string]$Path)
    $key = To-DisplayText $Path
    if ($script:MediaCache.ContainsKey($key)) {
        return $script:MediaCache[$key].Image
    }
    $bytes = Get-MediaBytes -Path $Path
    $stream = New-Object System.IO.MemoryStream(,$bytes)
    $image = [System.Drawing.Image]::FromStream($stream, $false, $false)
    $script:MediaCache[$key] = [pscustomobject]@{
        Image = $image
        Stream = $stream
        Touched = [DateTime]::UtcNow
    }
    return $image
}

function Queue-MediaPreload {
    param([object[]]$PreloadMedia = @())
    $queue = @()
    foreach ($entry in @($PreloadMedia)) {
        $text = (To-DisplayText $entry).Trim()
        if ($text -and -not $script:MediaCache.ContainsKey($text) -and $text -ne $script:CurrentMediaKey -and -not $queue.Contains($text)) {
            $queue += $text
        }
    }
    $script:PreloadQueue = $queue
}

function Consume-MediaPreload {
    param([int]$MaxItems = 1)
    if (-not $script:PreloadQueue -or $script:PreloadQueue.Count -le 0) {
        return
    }
    $take = [Math]::Min($MaxItems, $script:PreloadQueue.Count)
    $batch = @($script:PreloadQueue | Select-Object -First $take)
    $script:PreloadQueue = @($script:PreloadQueue | Select-Object -Skip $take)
    foreach ($path in $batch) {
        try {
            [void](Get-MediaImage -Path $path)
        } catch {
            Write-ViewerLog ("预加载失败: {0} | {1}" -f $path, $_.Exception.Message)
        }
    }
}

function Set-ViewerImage {
    param(
        [System.Windows.Forms.PictureBox]$PictureBox,
        [string]$Path,
        [object[]]$PreloadMedia = @()
    )
    $image = Get-MediaImage -Path $Path
    $PictureBox.Image = $image
    $script:CurrentMediaKey = To-DisplayText $Path
    Queue-MediaPreload -PreloadMedia $PreloadMedia
    $keep = @($script:CurrentMediaKey)
    foreach ($entry in @($PreloadMedia)) {
        $text = (To-DisplayText $entry).Trim()
        if ($text) {
            $keep += $text
        }
    }
    Clear-MediaCache -KeepKeys $keep
}

function Focus-ViewerWindow {
    param([System.Windows.Forms.Form]$Form)

    try {
        if ($script:ViewerActivated) {
            return
        }
        $script:ViewerActivated = $true
        $Form.TopMost = $true
    } catch {}
}

function Center-NavButtons {
    param(
        [System.Windows.Forms.Panel]$Panel,
        [System.Windows.Forms.Button]$PrevButton,
        [System.Windows.Forms.Button]$NextButton
    )

    $gap = 18
    $totalWidth = $PrevButton.Width + $NextButton.Width + $gap
    $left = [int](($Panel.ClientSize.Width - $totalWidth) / 2)
    if ($left -lt 0) {
        $left = 0
    }
    $PrevButton.Left = $left
    $PrevButton.Top = [int](($Panel.ClientSize.Height - $PrevButton.Height) / 2)
    $NextButton.Left = $PrevButton.Left + $PrevButton.Width + $gap
    $NextButton.Top = [int](($Panel.ClientSize.Height - $NextButton.Height) / 2)
}

function Set-ViewerContent {
    param(
        [pscustomobject]$Data,
        [System.Windows.Forms.Form]$Form,
        [System.Windows.Forms.Label]$TitleLabel,
        [System.Windows.Forms.Label]$StepLabel,
        [System.Windows.Forms.Label]$SummaryLabel,
        [System.Windows.Forms.Label]$NotesLabel,
        [System.Windows.Forms.Label]$TipsLabel,
        [System.Windows.Forms.Label]$DetailLabel,
        [System.Windows.Forms.Label]$MixLabel,
        [System.Windows.Forms.Label]$MediaLabel,
        [System.Windows.Forms.PictureBox]$PictureBox
    )

    $signature = @(
        To-DisplayText $Data.title
        To-DisplayText $Data.step_label
        To-DisplayText $Data.shape_key
        (($Data.media_files | ForEach-Object { To-DisplayText $_ }) -join "|")
        (($Data.detail_media_files | ForEach-Object { To-DisplayText $_ }) -join "|")
    ) -join "||"
    if ($script:ViewerSignature -ne $signature) {
        $script:ViewerSignature = $signature
        $script:ViewerMediaIndex = [Math]::Max(0, [Math]::Min([int]$Data.media_index, [Math]::Max(0, @($Data.media_files).Count - 1)))
    }

    $TitleLabel.Text = ("{0}  [{1}]" -f (To-DisplayText $Data.name_bilingual), (To-DisplayText $Data.category)).Trim()
    $StepLabel.Text = ("{0}    {1}" -f (To-DisplayText $Data.step_label), (To-DisplayText $Data.shape_key)).Trim()
    $SummaryLabel.Text = To-DisplayText $Data.summary
    $NotesLabel.Text = Get-BulletText $Data.notes
    $TipsLabel.Text = Get-BulletText $Data.tips
    $DetailLabel.Text = To-DisplayText $Data.detail_note
    $MixLabel.Text = Get-BulletText $Data.validation_mix

    try {
        $iconPath = To-DisplayText $Data.window_icon_path
        if ($iconPath -and (Test-Path -LiteralPath $iconPath)) {
            $Form.Icon = [System.Drawing.Icon]::ExtractAssociatedIcon($iconPath)
        }
    } catch {}

    $mediaFiles = @($Data.media_files)
    $preloadFiles = @($Data.preload_media_files)
    $mediaCount = $mediaFiles.Count
    $script:ViewerMediaCount = $mediaCount
    if ($mediaCount -le 0) {
        $MediaLabel.Text = "当前步骤没有参考图"
        $PictureBox.Image = $null
        return
    }

    $mediaIndex = [Math]::Max(0, [Math]::Min([int]$script:ViewerMediaIndex, $mediaCount - 1))
    $script:ViewerMediaIndex = $mediaIndex
    $path = [string]$mediaFiles[$mediaIndex]
    $MediaLabel.Text = "当前图 $($mediaIndex + 1) / $mediaCount    $(Split-Path $path -Leaf)"

    if ((-not (Test-RemoteUrl $path)) -and (-not (Test-Path -LiteralPath $path))) {
        $MediaLabel.Text = "未找到参考图: $path"
        $PictureBox.Image = $null
        return
    }

    try {
        Set-ViewerImage -PictureBox $PictureBox -Path $path -PreloadMedia $preloadFiles
    } catch {
        $MediaLabel.Text = "参考图加载失败: $path"
        $PictureBox.Image = $null
        Write-ViewerLog ("参考图加载失败: {0} | {1}" -f $path, $_.Exception.Message)
    }
}

function New-WrapLabel {
    param(
        [int]$Left,
        [int]$Top,
        [int]$Width,
        [int]$Height,
        [string]$FontName = "Microsoft YaHei UI",
        [single]$FontSize = 9,
        [System.Drawing.FontStyle]$FontStyle = [System.Drawing.FontStyle]::Regular
    )

    $label = New-Object System.Windows.Forms.Label
    $label.Left = $Left
    $label.Top = $Top
    $label.Width = $Width
    $label.Height = $Height
    $label.AutoSize = $false
    $label.Font = New-Object System.Drawing.Font($FontName, $FontSize, $FontStyle)
    $label.MaximumSize = New-Object System.Drawing.Size($Width, 0)
    $label.ForeColor = [System.Drawing.Color]::FromArgb(34, 34, 34)
    return $label
}

$uiBack = [System.Drawing.Color]::FromArgb(239, 239, 239)
$uiSurface = [System.Drawing.Color]::FromArgb(214, 214, 214)
$uiBorder = [System.Drawing.Color]::FromArgb(120, 120, 120)
$uiText = [System.Drawing.Color]::FromArgb(28, 28, 28)
$uiMuted = [System.Drawing.Color]::FromArgb(76, 76, 76)
$uiButton = [System.Drawing.Color]::FromArgb(186, 186, 186)
$uiButtonHover = [System.Drawing.Color]::FromArgb(200, 200, 200)

$form = New-Object System.Windows.Forms.Form
$form.Text = "ARKit Reference Viewer"
$form.ClientSize = New-Object System.Drawing.Size(500, 500)
$form.MinimumSize = New-Object System.Drawing.Size(500, 500)
$form.StartPosition = "Manual"
$form.TopMost = $true
$form.FormBorderStyle = "Sizable"
$form.BackColor = $uiBack
$form.ForeColor = $uiText
$form.Text = "ARKit 参考查看器"
$form.AutoScroll = $false
$form.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 9)

$titleLabel = New-WrapLabel -Left 12 -Top 10 -Width 460 -Height 28 -FontSize 11 -FontStyle ([System.Drawing.FontStyle]::Bold)
$titleLabel.ForeColor = $uiText
$form.Controls.Add($titleLabel)

$stepLabel = New-WrapLabel -Left 12 -Top 38 -Width 460 -Height 22
$stepLabel.ForeColor = $uiMuted
$form.Controls.Add($stepLabel)

$mediaLabel = New-WrapLabel -Left 12 -Top 58 -Width 460 -Height 22
$mediaLabel.ForeColor = $uiMuted
$form.Controls.Add($mediaLabel)

$pictureBox = New-Object System.Windows.Forms.PictureBox
$pictureBox.Left = 0
$pictureBox.Top = 0
$pictureBox.Width = 500
$pictureBox.Height = 500
$pictureBox.Anchor = "Top,Bottom,Left,Right"
$pictureBox.BackColor = $uiSurface
$pictureBox.SizeMode = "Zoom"
$pictureBox.MinimumSize = New-Object System.Drawing.Size(20, 20)
$form.Controls.Add($pictureBox)

$navPanel = New-Object System.Windows.Forms.Panel
$navPanel.Left = 0
$navPanel.Top = 456
$navPanel.Width = 500
$navPanel.Height = 34
$navPanel.Anchor = "Bottom,Left,Right"
$navPanel.BackColor = $uiBack
$form.Controls.Add($navPanel)
$navPanel.BringToFront()

$prevButton = New-Object System.Windows.Forms.Button
$prevButton.Width = 110
$prevButton.Height = 28
$prevButton.Text = "上一张图"
$prevButton.BackColor = $uiButton
$prevButton.ForeColor = $uiText
$prevButton.FlatStyle = "Flat"
$prevButton.FlatAppearance.BorderColor = $uiBorder
$prevButton.FlatAppearance.MouseOverBackColor = $uiButtonHover
$navPanel.Controls.Add($prevButton)

$nextButton = New-Object System.Windows.Forms.Button
$nextButton.Width = 110
$nextButton.Height = 28
$nextButton.Text = "下一张图"
$nextButton.BackColor = $uiButton
$nextButton.ForeColor = $uiText
$nextButton.FlatStyle = "Flat"
$nextButton.FlatAppearance.BorderColor = $uiBorder
$nextButton.FlatAppearance.MouseOverBackColor = $uiButtonHover
$navPanel.Controls.Add($nextButton)
$nextButton.BringToFront()
$prevButton.BringToFront()
Center-NavButtons -Panel $navPanel -PrevButton $prevButton -NextButton $nextButton
$navPanel.Add_Resize({
    Center-NavButtons -Panel $navPanel -PrevButton $prevButton -NextButton $nextButton
})

$summaryTitle = New-WrapLabel -Left 12 -Top 380 -Width 120 -Height 22 -FontSize 10 -FontStyle ([System.Drawing.FontStyle]::Bold)
$summaryTitle.ForeColor = $uiText
$form.Controls.Add($summaryTitle)
$summaryTitle.Text = "文本说明"

$summaryLabel = New-WrapLabel -Left 12 -Top 404 -Width 460 -Height 54
$summaryLabel.ForeColor = $uiText
$form.Controls.Add($summaryLabel)

$notesTitle = New-WrapLabel -Left 12 -Top 464 -Width 120 -Height 22 -FontSize 10 -FontStyle ([System.Drawing.FontStyle]::Bold)
$notesTitle.ForeColor = $uiText
$form.Controls.Add($notesTitle)
$notesTitle.Text = "注意事项"

$notesLabel = New-WrapLabel -Left 12 -Top 488 -Width 460 -Height 72
$notesLabel.ForeColor = $uiText
$form.Controls.Add($notesLabel)

$tipsTitle = New-WrapLabel -Left 12 -Top 568 -Width 120 -Height 22 -FontSize 10 -FontStyle ([System.Drawing.FontStyle]::Bold)
$tipsTitle.Text = "小技巧"
$tipsTitle.ForeColor = $uiText
$form.Controls.Add($tipsTitle)
$tipsTitle.Text = "小技巧"

$tipsLabel = New-WrapLabel -Left 12 -Top 592 -Width 460 -Height 50
$tipsLabel.ForeColor = $uiText
$form.Controls.Add($tipsLabel)

$detailTitle = New-WrapLabel -Left 12 -Top 656 -Width 180 -Height 22 -FontSize 10 -FontStyle ([System.Drawing.FontStyle]::Bold)
$detailTitle.ForeColor = $uiText
$form.Controls.Add($detailTitle)
$detailTitle.Text = "注意重点"

$detailLabel = New-WrapLabel -Left 12 -Top 680 -Width 460 -Height 60
$detailLabel.ForeColor = $uiText
$form.Controls.Add($detailLabel)

$mixTitle = New-WrapLabel -Left 12 -Top 748 -Width 140 -Height 22 -FontSize 10 -FontStyle ([System.Drawing.FontStyle]::Bold)
$mixTitle.ForeColor = $uiText
$form.Controls.Add($mixTitle)
$mixTitle.Text = "混合验证"

$mixLabel = New-WrapLabel -Left 12 -Top 772 -Width 460 -Height 52
$mixLabel.ForeColor = $uiText
$form.Controls.Add($mixLabel)

foreach ($control in @(
    $titleLabel,
    $stepLabel,
    $mediaLabel,
    $summaryTitle,
    $summaryLabel,
    $notesTitle,
    $notesLabel,
    $tipsTitle,
    $tipsLabel,
    $detailTitle,
    $detailLabel,
    $mixTitle,
    $mixLabel
)) {
    $control.Visible = $false
}

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 220
$preloadTimer = New-Object System.Windows.Forms.Timer
$preloadTimer.Interval = 350
$stateInfo = Get-Item -LiteralPath $StateFile
$script:StateLastWriteUtc = $stateInfo.LastWriteTimeUtc
$script:StateLastLength = $stateInfo.Length

function Invoke-ViewerUpdate {
    param([bool]$Force = $false)
    try {
        if (-not (Test-Path -LiteralPath $StateFile)) {
            return
        }
        $file = Get-Item -LiteralPath $StateFile
        if (
            -not $Force -and
            $file.LastWriteTimeUtc -eq $script:StateLastWriteUtc -and
            $file.Length -eq $script:StateLastLength -and
            $titleLabel.Text
        ) {
            return
        }
        $script:StateLastWriteUtc = $file.LastWriteTimeUtc
        $script:StateLastLength = $file.Length
        $json = Get-Content -LiteralPath $StateFile -Raw -Encoding UTF8
        $data = $json | ConvertFrom-Json
        Set-ViewerContent -Data $data -Form $form -TitleLabel $titleLabel -StepLabel $stepLabel -SummaryLabel $summaryLabel -NotesLabel $notesLabel -TipsLabel $tipsLabel -DetailLabel $detailLabel -MixLabel $mixLabel -MediaLabel $mediaLabel -PictureBox $pictureBox
        if ($Force) {
            Focus-ViewerWindow -Form $form
        }
    } catch {}
}

$prevButton.Add_Click({
    try {
        if ($script:ViewerMediaCount -le 0) {
            return
        }
        $script:ViewerMediaIndex = ($script:ViewerMediaIndex - 1 + $script:ViewerMediaCount) % $script:ViewerMediaCount
        Invoke-ViewerUpdate -Force $true
    } catch {}
})

$nextButton.Add_Click({
    try {
        if ($script:ViewerMediaCount -le 0) {
            return
        }
        $script:ViewerMediaIndex = ($script:ViewerMediaIndex + 1) % $script:ViewerMediaCount
        Invoke-ViewerUpdate -Force $true
    } catch {}
})

$timer.Add_Tick({
    Invoke-ViewerUpdate -Force $false
})
$preloadTimer.Add_Tick({
    Consume-MediaPreload -MaxItems 1
})
$form.Add_Shown({
    try {
        [System.IO.File]::WriteAllText($PidFile, [System.Diagnostics.Process]::GetCurrentProcess().Id.ToString(), [System.Text.Encoding]::UTF8)
    } catch {}
    Invoke-ViewerUpdate -Force $true
    $timer.Start()
    $preloadTimer.Start()
})

$form.Add_FormClosed({
    $timer.Stop()
    $preloadTimer.Stop()
    Stop-CurrentMedia
    try {
        Remove-Item -LiteralPath $StateFile -Force -ErrorAction SilentlyContinue
    } catch {}
    try {
        if ((Test-Path -LiteralPath $PidFile) -and ((Get-Content -LiteralPath $PidFile -Raw -Encoding UTF8).Trim() -eq [System.Diagnostics.Process]::GetCurrentProcess().Id.ToString())) {
            Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        }
    } catch {}
})

try {
    [void]$form.ShowDialog()
} catch {
Write-ViewerLog ("参考窗口启动失败: {0}" -f $_.Exception.Message)
$errorForm = New-Object System.Windows.Forms.Form
$errorForm.Text = "ARKit 参考查看器"
$errorForm.ClientSize = New-Object System.Drawing.Size(500, 500)
$errorForm.TopMost = $true
$errorForm.FormBorderStyle = "SizableToolWindow"
$errorForm.BackColor = $uiBack
$errorForm.ForeColor = $uiText
$errorLabel = New-WrapLabel -Left 12 -Top 12 -Width 460 -Height 420 -FontSize 10
$errorLabel.Text = "参考窗口启动失败: $($_.Exception.Message)"
$errorLabel.ForeColor = $uiText
$errorForm.Controls.Add($errorLabel)
$okButton = New-Object System.Windows.Forms.Button
$okButton.Width = 110
$okButton.Height = 28
$okButton.Text = "关闭"
$okButton.BackColor = $uiSurface
$okButton.ForeColor = $uiText
$okButton.FlatStyle = "System"
$okButton.Left = [int](($errorForm.ClientSize.Width - $okButton.Width) / 2)
$okButton.Top = 440
$okButton.Add_Click({ $errorForm.Close() })
$errorForm.Controls.Add($okButton)
    [void]$errorForm.ShowDialog()
}

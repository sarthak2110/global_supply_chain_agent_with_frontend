document.addEventListener("DOMContentLoaded", () => {
    // Create the 4 floating orbs defined in your new CSS
    const orbIds = ["up", "down", "left", "right"];
    
    orbIds.forEach(id => {
        const orb = document.createElement("div");
        orb.id = id;
        document.body.appendChild(orb);
    });
});


// document.addEventListener("DOMContentLoaded", () => {
//     // 1. Create the blob element and inject it into the background
//     const blob = document.createElement("div");
//     blob.classList.add("blob");
//     document.body.appendChild(blob);

//     // 2. Animation Logic
//     let target = { x: window.innerWidth / 2, y: window.innerHeight / 2 };
//     let current = { x: target.x, y: target.y };

//     function newTarget() {
//         target.x = Math.random() * window.innerWidth;
//         target.y = Math.random() * window.innerHeight;
//     }

//     function animate() {
//         current.x += (target.x - current.x) * 0.02; // Speed factor
//         current.y += (target.y - current.y) * 0.02;

//         blob.style.transform = `translate3d(calc(${current.x}px - 50%), calc(${current.y}px - 50%), 0)`;

//         if (Math.abs(current.x - target.x) < 1 && Math.abs(current.y - target.y) < 1) {
//             newTarget();
//         }

//         requestAnimationFrame(animate);
//     }

//     animate();

//     window.addEventListener('resize', () => {
//         target.x = Math.random() * window.innerWidth;
//         target.y = Math.random() * window.innerHeight;
//     });
// });